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


if __name__ == "__main__":
    unittest.main()
