from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_epub_validation_report import generate_epub_validation_report
from test_epub_validation import _build_epub


class EpubValidationReportTests(unittest.TestCase):
    def test_missing_epub_path_writes_failed_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_json = Path(temp_dir) / "reports" / "epub_validation.json"
            output_md = Path(temp_dir) / "reports" / "epub_validation.md"

            payload = generate_epub_validation_report(output_json=output_json, output_md=output_md)

        self.assertEqual(payload["status"], "failed")
        self.assertFalse(payload["release_ready"])
        self.assertEqual(payload["errors"][0]["code"], "epub_path_missing")

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_valid_epub_path_writes_passed_report(self, _mock_epubcheck) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub = root / "book.epub"
            epub.write_bytes(_build_epub(chapter_body='<h1 id="intro">Intro</h1><p>Body.</p>'))
            output_json = root / "reports" / "epub_validation.json"
            output_md = root / "reports" / "epub_validation.md"

            payload = generate_epub_validation_report(epub, output_json=output_json, output_md=output_md)

            self.assertTrue(output_json.exists())
            self.assertTrue(output_md.exists())

        self.assertEqual(payload["status"], "passed")
        self.assertTrue(payload["release_ready"])
        self.assertEqual(payload["summary"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
