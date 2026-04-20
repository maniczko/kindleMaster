import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from app import app


class AppHeadingRepairTests(unittest.TestCase):
    def test_convert_keeps_base_epub_when_heading_repair_fails(self):
        client = app.test_client()
        base_epub = b"base-epub"
        fake_result = {
            "epub_bytes": base_epub,
            "analysis": {"profile": "book_reflow", "confidence": 0.92, "legacy_strategy": "text_reflowable"},
            "quality_report": {
                "validation_status": "passed",
                "validation_tool": "epubcheck",
                "warnings": [],
                "high_risk_pages": [],
                "high_risk_sections": [],
            },
            "document_summary": {
                "title": "Raport koncowy",
                "author": "Jan Kowalski",
                "profile": "book_reflow",
                "layout_mode": "reflowable",
                "section_count": 5,
                "asset_count": 1,
            },
        }
        fake_heading_repair = SimpleNamespace(
            epub_bytes=b"repaired-epub",
            summary={
                "release_status": "fail",
                "toc_entries_before": 2,
                "toc_entries_after": 6,
                "headings_removed": 1,
                "manual_review_count": 4,
                "epubcheck_status": "failed",
            },
            epubcheck={
                "status": "failed",
                "messages": [
                    "Validating using EPUB version 3.3 rules.",
                    "ERROR(RSC-007): missing target",
                ],
            },
        )

        with patch("app.convert_document_to_epub_with_report", return_value=fake_result):
            with patch("app.repair_epub_headings_and_toc", return_value=fake_heading_repair):
                response = client.post(
                    "/convert",
                    data={
                        "pdf": (io.BytesIO(b"%PDF-1.4\n%synthetic\n"), "sample.pdf"),
                        "profile": "auto-premium",
                        "ocr": "false",
                        "language": "pl",
                        "heading_repair": "true",
                    },
                    content_type="multipart/form-data",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, base_epub)
        self.assertEqual(response.headers.get("X-Heading-Repair-Status"), "failed")
        self.assertEqual(response.headers.get("X-Heading-Repair-EPUBCheck"), "failed")
        self.assertIn("missing target", unquote(response.headers.get("X-Heading-Repair-Error", "")))


if __name__ == "__main__":
    unittest.main()
