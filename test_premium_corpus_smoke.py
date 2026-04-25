import io
import unittest
import zipfile
from pathlib import Path

from premium_corpus_smoke import (
    CorpusCase,
    _apply_release_strictness,
    _build_case_blockers,
    _build_case_warnings,
    _build_release_fallback_signal,
    _derive_case_grade,
    inspect_epub,
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


if __name__ == "__main__":
    unittest.main()
