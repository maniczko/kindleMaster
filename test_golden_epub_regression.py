from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from golden_epub_regression import run_golden_epub_regression


def _build_epub(
    path: Path,
    *,
    title: str = "Golden Probe",
    creator: str = "Golden Author",
    language: str = "en",
    nav_labels: list[str] | None = None,
    body: str = "",
) -> None:
    nav_labels = nav_labels or ["Chapter"]
    nav_items = "".join(
        f'<li><a href="chapter.xhtml#s{i}">{label}</a></li>' for i, label in enumerate(nav_labels, start=1)
    )
    headings = "".join(f'<h1 id="s{i}">{label}</h1><p>Clean paragraph text.</p>' for i, label in enumerate(nav_labels, start=1))
    chapter_body = body or headings
    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:golden-probe</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{creator}</dc:creator>
    <dc:language>{language}</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>TOC</title></head>
  <body>
    <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
      <ol>{nav_items}</ol>
    </nav>
  </body>
</html>
"""
    chapter_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter</title></head>
  <body>{chapter_body}</body>
</html>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""")
        archive.writestr("EPUB/content.opf", content_opf)
        archive.writestr("EPUB/nav.xhtml", nav_xhtml)
        archive.writestr("EPUB/chapter.xhtml", chapter_xhtml)
    path.write_bytes(buffer.getvalue())


class GoldenEpubRegressionTests(unittest.TestCase):
    def test_golden_regression_passes_on_expected_epub_features(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"
            reports_dir = root / "reports"
            artifact_root.mkdir()
            _build_epub(
                artifact_root / "book.epub",
                nav_labels=["Intro", "Chapter"],
                body='<h1 id="s1">Intro</h1><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>',
            )
            manifest = root / "golden.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": [
                            {
                                "id": "book",
                                "document_class": "book",
                                "input_type": "pdf",
                                "artifact_candidates": ["book.epub"],
                                "expectations": {
                                    "language": "en",
                                    "min_xhtml_count": 1,
                                    "min_nav_entries": 2,
                                    "min_table_count": 1,
                                    "max_table_columns": 3,
                                    "max_artifact_rate_per_1000_words": 1.0,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "golden_epub_regression.validate_epub_path",
                return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "document_stats": {}},
            ):
                payload = run_golden_epub_regression(
                    manifest_path=manifest,
                    artifact_root=artifact_root,
                    reports_dir=reports_dir,
                )

            self.assertEqual(payload["status"], "passed")
            self.assertTrue((reports_dir / "golden_epub_regression.json").exists())
            self.assertTrue((reports_dir / "golden_epub_regression.md").exists())

    def test_golden_regression_fails_on_noisy_toc_or_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            _build_epub(artifact_root / "noisy.epub", nav_labels=["Input"])
            manifest = root / "golden.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": [
                            {
                                "id": "noisy",
                                "document_class": "book",
                                "input_type": "pdf",
                                "artifact_candidates": ["noisy.epub"],
                                "expectations": {"language": "en", "min_nav_entries": 1},
                            },
                            {
                                "id": "missing",
                                "document_class": "book",
                                "input_type": "pdf",
                                "artifact_candidates": ["missing.epub"],
                                "expectations": {"language": "en"},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "golden_epub_regression.validate_epub_path",
                return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "document_stats": {}},
            ):
                payload = run_golden_epub_regression(
                    manifest_path=manifest,
                    artifact_root=artifact_root,
                    reports_dir=root / "reports",
                )

            self.assertEqual(payload["status"], "failed")
            statuses = {case["case_id"]: case["status"] for case in payload["cases"]}
            self.assertEqual(statuses["noisy"], "failed")
            self.assertEqual(statuses["missing"], "failed")

    def test_golden_regression_reports_before_after_deltas_and_sendability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"
            reports_dir = root / "reports"
            artifact_root.mkdir()
            _build_epub(artifact_root / "before.epub", nav_labels=["Intro"])
            _build_epub(artifact_root / "after.epub", nav_labels=["Intro", "Chapter"])
            manifest = root / "golden.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": [
                            {
                                "id": "compare",
                                "document_class": "book",
                                "input_type": "pdf",
                                "artifact_candidates": ["after.epub"],
                                "before_artifact_candidates": ["before.epub"],
                                "expectations": {
                                    "language": "en",
                                    "min_xhtml_count": 1,
                                    "min_nav_entries": 2,
                                    "max_artifact_rate_per_1000_words": 1.0,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "golden_epub_regression.validate_epub_path",
                return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "document_stats": {}},
            ):
                payload = run_golden_epub_regression(
                    manifest_path=manifest,
                    artifact_root=artifact_root,
                    reports_dir=reports_dir,
                )

            case = payload["cases"][0]
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(case["features"]["kindle_sendability_status"], "sendable")
            self.assertEqual(case["comparison"]["metrics"]["nav_entries"]["status"], "improved")
            self.assertEqual(case["comparison"]["status"], "improved")
            markdown = (reports_dir / "golden_epub_regression.md").read_text(encoding="utf-8")
            self.assertIn("Before/after", markdown)
            self.assertIn("Kindle sendability", markdown)

    def test_golden_regression_skips_optional_local_only_cases_when_artifact_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = root / "golden.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": [
                            {
                                "id": "private-real-case",
                                "document_class": "magazine",
                                "input_type": "pdf",
                                "required": False,
                                "artifact_candidates": ["private-real-case.epub"],
                                "expectations": {"language": "en"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = run_golden_epub_regression(
                manifest_path=manifest,
                artifact_root=root / "missing-artifacts",
                reports_dir=root / "reports",
            )

            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["status_counts"], {"skipped": 1})
            self.assertEqual(payload["cases"][0]["status"], "skipped")

    def test_golden_regression_skips_optional_real_fixture_case_when_only_tiny_surrogate_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_root = root / "artifacts"
            reports_dir = root / "reports"
            artifact_root.mkdir()
            _build_epub(artifact_root / "tiny-surrogate.epub", nav_labels=["Intro"])
            manifest = root / "golden.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": [
                            {
                                "id": "real_only",
                                "document_class": "magazine_issue",
                                "input_type": "pdf",
                                "required": False,
                                "requires_real_fixture": True,
                                "minimum_artifact_size_bytes": 100_000,
                                "artifact_candidates": ["tiny-surrogate.epub"],
                                "expectations": {"language": "en", "min_nav_entries": 20},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = run_golden_epub_regression(
                manifest_path=manifest,
                artifact_root=artifact_root,
                reports_dir=reports_dir,
            )

            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["status_counts"], {"skipped": 1})
            self.assertEqual(payload["cases"][0]["assertions"][0]["id"], "real_fixture_available")
            self.assertIn("minimum_real_fixture_bytes=100000", payload["cases"][0]["assertions"][0]["detail"])

    def test_golden_regression_uses_env_artifact_root_for_private_fixture_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_root = root / "env-artifacts"
            env_root.mkdir()
            _build_epub(env_root / "env-case.epub", nav_labels=["Intro"])
            manifest = root / "golden.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "cases": [
                            {
                                "id": "env-case",
                                "document_class": "book",
                                "input_type": "pdf",
                                "artifact_candidates": ["env-case.epub"],
                                "expectations": {"language": "en", "min_nav_entries": 1},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"KINDLEMASTER_GOLDEN_ROOT": str(env_root)}), patch(
                "golden_epub_regression.validate_epub_path",
                return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "document_stats": {}},
            ):
                payload = run_golden_epub_regression(
                    manifest_path=manifest,
                    artifact_root=root / "wrong-root",
                    reports_dir=root / "reports",
                )

            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["artifact_root"], str(env_root))
            self.assertEqual(payload["artifact_root_source"], "env")


if __name__ == "__main__":
    unittest.main()
