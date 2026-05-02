import io
import unittest
import zipfile
from pathlib import Path

from premium_corpus_smoke import (
    CorpusCase,
    _apply_release_strictness,
    _build_ocr_benchmark_payload,
    _build_overall_summary,
    _build_case_blockers,
    _build_case_warnings,
    _build_class_coverage,
    _ocr_benchmark_findings,
    _build_release_fallback_signal,
    _derive_case_grade,
    build_output_assertions,
    inspect_epub,
    CorpusSourceSummary,
)


class PremiumCorpusSmokeTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, content.encode("utf-8"), compress_type=compress_type)
        return output.getvalue()

    def test_inspect_epub_extracts_metadata_nav_and_junk(self):
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""",
                "EPUB/content.opf": """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Executive summary</dc:title>
    <dc:creator>Unknown</dc:creator>
    <dc:language>pl</dc:language>
  </metadata>
</package>
""",
                "EPUB/nav.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter_001.xhtml#intro">Intro</a>
          <ol><li><a href="chapter_001.xhtml#details">Details</a></li></ol>
        </li>
      </ol>
    </nav>
  </body>
</html>
""",
                "EPUB/chapter_001.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="intro">Intro</h1>
    <p>Link requires manual review. Broken source https://the and more text.</p>
    <a href="#missing-anchor">broken</a>
  </body>
</html>
""",
            }
        )

        stats = inspect_epub(epub_bytes)

        self.assertEqual(stats["package_title"], "Executive summary")
        self.assertEqual(stats["package_creator"], "Unknown")
        self.assertTrue(stats["metadata_placeholder_title"])
        self.assertTrue(stats["metadata_placeholder_creator"])
        self.assertEqual(stats["nav_entries"], 2)
        self.assertEqual(stats["nav_depth"], 2)
        self.assertEqual(stats["visible_junk_counts"]["manual_review_label"], 1)
        self.assertEqual(stats["visible_junk_counts"]["half_url_https_the"], 1)
        self.assertEqual(stats["broken_internal_anchors"], 1)

    def test_case_gate_marks_blockers_and_review(self):
        blockers = _build_case_blockers(
            quality={"validation_status": "failed"},
            inspect={
                "visible_junk_counts": {"manual_review_label": 1},
                "broken_href_counts": {"half_url_https_the": 1},
                "broken_internal_anchors": 1,
                "metadata_placeholder_title": True,
                "package_title": "Executive summary",
                "metadata_placeholder_creator": True,
                "package_creator": "Unknown",
            },
            heading_summary={"epubcheck_status": "failed"},
        )
        warnings = _build_case_warnings(
            summary={"section_count": 10},
            quality={"text_cleanup": {"review_needed_count": 250, "blocked_count": 700}},
            inspect={"nav_entries": 2, "package_language": "de"},
            heading_summary={"release_status": "pass_with_review", "manual_review_count": 5},
        )

        blocker_codes = {item["code"] for item in blockers}
        warning_codes = {item["code"] for item in warnings}

        self.assertIn("epubcheck_failed", blocker_codes)
        self.assertIn("visible_reference_or_url_junk", blocker_codes)
        self.assertIn("broken_href_patterns", blocker_codes)
        self.assertIn("placeholder_title", blocker_codes)
        self.assertIn("placeholder_creator", blocker_codes)
        self.assertIn("heading_repair_epubcheck_failed", blocker_codes)

        self.assertIn("high_review_noise", warning_codes)
        self.assertIn("high_blocked_noise", warning_codes)
        self.assertIn("shallow_toc", warning_codes)
        self.assertIn("heading_manual_review", warning_codes)
        self.assertIn("unexpected_language", warning_codes)
        self.assertEqual(_derive_case_grade(blockers, warnings), "fail")

    def test_reference_cleanup_gate_failures_are_release_blockers(self) -> None:
        blockers = _build_case_blockers(
            quality={
                "validation_status": "passed",
                "text_cleanup": {
                    "reference_cleanup": {
                        "reference_quality_gate_status": "failed",
                        "citations_missing_record": 2,
                        "citations_ambiguous": 1,
                        "unresolved_fragment_count": 3,
                        "empty_reference_sections_unresolved": 1,
                    }
                },
            },
            inspect={
                "visible_junk_counts": {},
                "broken_href_counts": {},
                "broken_internal_anchors": 0,
                "metadata_placeholder_title": False,
                "metadata_placeholder_creator": False,
            },
            heading_summary={"epubcheck_status": "passed"},
        )

        blocker_codes = [item["code"] for item in blockers]

        self.assertEqual(
            blocker_codes,
            [
                "reference_quality_gate_failed",
                "reference_citations_missing",
                "reference_citations_ambiguous",
                "reference_unresolved_fragments",
                "reference_empty_section_unresolved",
            ],
        )
        self.assertEqual(_derive_case_grade(blockers, []), "fail")

    def test_reference_cleanup_review_signals_remain_review_warnings(self) -> None:
        warnings = _build_case_warnings(
            summary={"section_count": 3},
            quality={
                "validation_status": "passed",
                "reference_cleanup": {
                    "quality_gate_status": "passed",
                    "records_flagged_for_review": 3,
                    "review_entry_count": 2,
                },
            },
            inspect={"nav_entries": 3, "package_language": "pl"},
            heading_summary={"release_status": "pass", "epubcheck_status": "passed"},
        )

        self.assertEqual([item["code"] for item in warnings], ["reference_review_needed"])
        self.assertEqual(_derive_case_grade([], warnings), "pass_with_review")

    def test_empty_reference_section_without_citations_is_warning_not_blocker(self) -> None:
        quality = {
            "validation_status": "passed",
            "text_cleanup": {
                "reference_cleanup": {
                    "reference_quality_gate_status": "passed_with_warnings",
                    "citations_detected": 0,
                    "empty_reference_sections_unresolved": 1,
                }
            },
        }
        inspect = {
            "visible_junk_counts": {},
            "broken_href_counts": {},
            "broken_internal_anchors": 0,
            "metadata_placeholder_title": False,
            "metadata_placeholder_creator": False,
        }

        blockers = _build_case_blockers(
            quality=quality,
            inspect=inspect,
            heading_summary={"epubcheck_status": "passed"},
        )
        warnings = _build_case_warnings(
            summary={"section_count": 3},
            quality=quality,
            inspect={**inspect, "nav_entries": 3, "package_language": "pl"},
            heading_summary={"release_status": "pass", "epubcheck_status": "passed"},
        )

        self.assertNotIn("reference_empty_section_unresolved", [item["code"] for item in blockers])
        self.assertIn("reference_empty_section_review", [item["code"] for item in warnings])
        self.assertEqual(_derive_case_grade(blockers, warnings), "pass_with_review")

    def test_text_artifact_rate_failed_is_release_blocker(self) -> None:
        quality = {
            "validation_status": "passed",
            "text_cleanup": {
                "artifact_rate": {
                    "status": "failed",
                    "artifact_count": 9,
                    "artifact_rate_per_1000_words": 6.25,
                }
            },
        }

        blockers = _build_case_blockers(
            quality=quality,
            inspect={
                "visible_junk_counts": {},
                "broken_href_counts": {},
                "broken_internal_anchors": 0,
                "metadata_placeholder_title": False,
                "metadata_placeholder_creator": False,
            },
            heading_summary={"epubcheck_status": "passed"},
        )

        self.assertIn("text_artifact_rate_failed", [item["code"] for item in blockers])
        self.assertEqual(_derive_case_grade(blockers, []), "fail")

    def test_text_artifact_rate_warning_is_corpus_review_signal(self) -> None:
        warnings = _build_case_warnings(
            summary={"section_count": 3},
            quality={
                "validation_status": "passed",
                "text_cleanup": {
                    "artifact_rate": {
                        "status": "passed_with_warnings",
                        "artifact_count": 2,
                        "artifact_rate_per_1000_words": 2.5,
                    }
                },
            },
            inspect={"nav_entries": 3, "package_language": "en"},
            heading_summary={"release_status": "pass", "epubcheck_status": "passed"},
        )

        self.assertEqual([item["code"] for item in warnings], ["text_artifact_rate_review"])
        self.assertEqual(_derive_case_grade([], warnings), "pass_with_review")

    def test_pre_heading_epubcheck_failure_recovered_by_heading_repair_is_review_not_blocker(self) -> None:
        quality = {"validation_status": "failed"}
        heading_summary = {"status": "completed", "epubcheck_status": "passed", "release_status": "pass"}

        blockers = _build_case_blockers(
            quality=quality,
            inspect={
                "visible_junk_counts": {},
                "broken_href_counts": {},
                "broken_internal_anchors": 0,
                "metadata_placeholder_title": False,
                "metadata_placeholder_creator": False,
            },
            heading_summary=heading_summary,
        )
        warnings = _build_case_warnings(
            summary={"section_count": 3},
            quality=quality,
            inspect={"nav_entries": 3, "package_language": "en"},
            heading_summary=heading_summary,
        )

        self.assertEqual(blockers, [])
        self.assertIn("pre_heading_epubcheck_recovered", {item["code"] for item in warnings})
        self.assertEqual(_derive_case_grade(blockers, warnings), "pass_with_review")

    def test_epub_validation_warning_is_review_not_blocker(self) -> None:
        quality = {"validation_status": "passed_with_warnings", "validation_summary": "epubcheck unavailable"}
        heading_summary = {"status": "completed", "epubcheck_status": "unavailable", "release_status": "pass"}

        blockers = _build_case_blockers(
            quality=quality,
            inspect={
                "visible_junk_counts": {},
                "broken_href_counts": {},
                "broken_internal_anchors": 0,
                "metadata_placeholder_title": False,
                "metadata_placeholder_creator": False,
            },
            heading_summary=heading_summary,
        )
        warnings = _build_case_warnings(
            summary={"section_count": 3},
            quality=quality,
            inspect={"nav_entries": 3, "package_language": "en"},
            heading_summary=heading_summary,
        )

        self.assertEqual(blockers, [])
        self.assertIn("epub_validation_warning", {item["code"] for item in warnings})
        self.assertEqual(_derive_case_grade(blockers, warnings), "pass_with_review")

    def test_legacy_fallback_signal_is_strictness_aware(self) -> None:
        strict_case = CorpusCase(path=Path("example/input.pdf"), document_class="book", release_strict=True)
        relaxed_case = CorpusCase(path=Path("example/input.pdf"), document_class="probe", release_strict=False)

        strict_signal = _build_release_fallback_signal(
            analysis={"profile": "legacy-fallback", "profile_reason": "premium failed"},
            quality={"validation_tool": "legacy"},
            case=strict_case,
        )
        relaxed_signal = _build_release_fallback_signal(
            analysis={"profile": "legacy-fallback", "profile_reason": "premium failed"},
            quality={"validation_tool": "legacy"},
            case=relaxed_case,
        )

        self.assertTrue(strict_signal["used"])
        self.assertEqual(strict_signal["severity"], "blocker")
        self.assertEqual(relaxed_signal["severity"], "warning")

    def test_non_release_strict_probe_relaxes_placeholder_metadata_and_heading_review(self) -> None:
        case = CorpusCase(path=Path("reference_inputs/pdf/ocr_probe.pdf"), document_class="ocr_probe", release_strict=False)
        blockers = [
            {"code": "placeholder_creator", "detail": "Unknown"},
            {"code": "broken_internal_anchors", "detail": "1"},
        ]
        warnings = [
            {"code": "heading_manual_review", "detail": "manual_review_count=4"},
            {"code": "unexpected_language", "detail": "de"},
        ]

        relaxed_blockers, relaxed_warnings = _apply_release_strictness(
            case,
            blockers=blockers,
            warnings=warnings,
        )

        self.assertEqual(
            [item["code"] for item in relaxed_blockers],
            ["broken_internal_anchors"],
        )
        self.assertEqual(
            [item["code"] for item in relaxed_warnings],
            ["unexpected_language"],
        )

    def test_ocr_benchmark_payload_reports_degraded_capability_and_reason_codes(self) -> None:
        case = CorpusCase(Path("reference_inputs/pdf/ocr_stress_scan.pdf"), document_class="ocr_stress_scan")
        quality = {
            "ocr_quality": {
                "status": "degraded",
                "reason_codes": ["ocr_unavailable", "pymupdf_fallback"],
                "fallback_reason": "ocr_unavailable",
                "manual_review_count": 2,
                "low_confidence_page_count": 1,
                "empty_ocr_page_count": 1,
            },
            "text_cleanup": {
                "artifact_rate": {
                    "artifact_rate_per_1000_words": 4.5,
                    "artifact_count": 9,
                }
            },
        }

        payload = _build_ocr_benchmark_payload(
            case=case,
            quality=quality,
            inspect={"xhtml_count": 2, "image_count": 1},
        )
        blockers, warnings = _ocr_benchmark_findings(case, payload)

        self.assertEqual(payload["capability_status"], "degraded")
        self.assertIn("ocr_unavailable", payload["reason_codes"])
        self.assertEqual(payload["artifact_rate_per_1000_words"], 4.5)
        self.assertEqual([item["code"] for item in blockers], ["ocr_capability_degraded", "ocr_quality_review"])
        self.assertEqual(warnings, [])

    def test_ocr_benchmark_overall_summary_counts_scan_cases(self) -> None:
        rows = [
            {
                "mode": "convert-and-audit",
                "document_class": "ocr_stress_scan",
                "input_type": "pdf",
                "grade": "pass_with_review",
                "blockers": [],
                "warnings": [{"code": "ocr_capability_degraded"}],
                "ocr_benchmark": {"capability_status": "degraded"},
            },
            {
                "mode": "convert-and-audit",
                "document_class": "mixed_scan_text",
                "input_type": "pdf",
                "grade": "pass",
                "blockers": [],
                "warnings": [],
                "ocr_benchmark": {"capability_status": "supported"},
            },
        ]

        overall = _build_overall_summary(rows)

        self.assertEqual(overall["ocr_benchmark"]["case_count"], 2)
        self.assertEqual(overall["ocr_benchmark"]["capability_counts"], {"degraded": 1, "supported": 1})

    def test_output_assertions_are_generic_by_document_class_route(self) -> None:
        stats = {
            "xhtml_count": 2,
            "image_count": 1,
            "nav_entries": 2,
            "package_language": "en",
            "heading_counts": {"h1": 1},
        }

        docx_assertions = build_output_assertions(
            document_class="docx_rich_content",
            input_type="docx",
            stats=stats,
            validation_status="passed",
        )
        diagram_assertions = build_output_assertions(
            document_class="diagram_training_book",
            input_type="pdf",
            stats={**stats, "image_count": 0},
            validation_status="passed",
        )

        self.assertFalse([item for item in docx_assertions if item["status"] == "failed"])
        self.assertIn("docx_sections_materialized", {item["id"] for item in docx_assertions})
        failed_diagram = [item for item in diagram_assertions if item["status"] == "failed"]
        self.assertEqual([item["id"] for item in failed_diagram], ["diagram_output_has_images"])

    def test_class_coverage_exposes_converted_analysis_only_and_skipped_reasons(self) -> None:
        rows = [
            {
                "case_id": "report_pdf",
                "file": "report.pdf",
                "document_class": "document-like-report",
                "input_type": "pdf",
                "mode": "convert-and-audit",
                "grade": "pass",
            },
            {
                "case_id": "large_diagram",
                "file": "large.pdf",
                "document_class": "large-diagram-corpus",
                "input_type": "pdf",
                "mode": "analysis-only",
                "analysis_only_reason": "Large analysis-only stress case for profile detection.",
            },
        ]
        source_summary = CorpusSourceSummary(
            source_mode="manifest-backed",
            manifest_path="manifest.json",
            manifest_case_count=3,
            eligible_manifest_cases=1,
            skipped_manifest_cases=1,
            skipped_case_labels=("simple_report_docx (docx)",),
            fallback_used=False,
            fallback_reason="",
            skipped_case_reasons=(
                {
                    "id": "simple_report_docx",
                    "document_class": "docx_structured_report",
                    "input_type": "docx",
                    "reason": "premium_pdf_only",
                    "detail": "Covered by smoke.",
                },
            ),
        )

        coverage = _build_class_coverage(rows, source_summary=source_summary)

        self.assertEqual(coverage["converted_classes"], ["document-like-report"])
        self.assertEqual(coverage["analysis_only_classes"], ["large-diagram-corpus"])
        self.assertEqual(coverage["skipped_classes"], ["docx_structured_report"])
        self.assertEqual(coverage["converted_focus_routes"], ["dense_report"])
        self.assertEqual(coverage["analysis_only_focus_routes"], ["diagram_chess"])
        self.assertEqual(coverage["skipped_focus_routes"], ["docx"])
        self.assertEqual(coverage["skipped_cases"][0]["reason"], "premium_pdf_only")


if __name__ == "__main__":
    unittest.main()
