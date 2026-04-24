import io
import unittest
from unittest.mock import patch

from docx import Document

from app import app


def _docx_bytes() -> bytes:
    document = Document()
    document.core_properties.title = "Docx Probe"
    document.core_properties.author = "Codex QA"
    document.add_heading("Docx Probe", level=1)
    document.add_paragraph("Probe paragraph.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class AppDocxConversionTests(unittest.TestCase):
    def test_analyze_accepts_docx_and_returns_docx_payload(self) -> None:
        client = app.test_client()
        fake_analysis = {
            "source_type": "docx",
            "profile": "docx_reflow",
            "paragraph_count": 12,
            "heading1_count": 1,
            "heading2_count": 2,
            "heading3_count": 0,
            "list_count": 1,
            "table_count": 1,
            "image_count": 0,
            "hyperlink_count": 1,
            "estimated_sections": 1,
            "publication_analysis": {
                "profile": "docx_reflow",
                "confidence": 0.96,
                "has_toc": True,
                "external_tools": {},
                "profile_reason": "DOCX structure detected.",
            },
        }

        with patch("app.analyze_docx", return_value=fake_analysis):
            response = client.post(
                "/analyze",
                data={"file": (io.BytesIO(_docx_bytes()), "sample.docx")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["source_type"], "docx")
        self.assertEqual(payload["analysis"]["heading1_count"], 1)

    def test_analyze_accepts_uppercase_docx_extension(self) -> None:
        client = app.test_client()
        fake_analysis = {
            "source_type": "docx",
            "profile": "docx_reflow",
            "paragraph_count": 4,
            "heading1_count": 1,
            "heading2_count": 0,
            "heading3_count": 0,
            "list_count": 0,
            "table_count": 0,
            "image_count": 0,
            "hyperlink_count": 0,
            "estimated_sections": 1,
            "publication_analysis": {
                "profile": "docx_reflow",
                "confidence": 0.93,
                "has_toc": False,
                "external_tools": {},
                "profile_reason": "DOCX structure detected.",
            },
        }

        with patch("app.analyze_docx", return_value=fake_analysis):
            response = client.post(
                "/analyze",
                data={"file": (io.BytesIO(_docx_bytes()), "SAMPLE.DOCX")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["source_type"], "docx")

    def test_convert_accepts_docx_and_sets_source_header(self) -> None:
        client = app.test_client()
        fake_result = {
            "epub_bytes": b"epub-docx",
            "source_type": "docx",
            "analysis": {"profile": "docx_reflow", "confidence": 0.95},
            "quality_report": {
                "validation_status": "passed",
                "validation_tool": "epubcheck",
                "warnings": [],
                "high_risk_pages": [],
                "high_risk_sections": [],
            },
            "document_summary": {
                "title": "Docx Probe",
                "author": "Codex QA",
                "profile": "docx_reflow",
                "layout_mode": "reflowable",
                "section_count": 2,
                "asset_count": 1,
            },
        }

        with patch("app.convert_document_to_epub_with_report", return_value=fake_result):
            response = client.post(
                "/convert",
                data={
                    "file": (io.BytesIO(_docx_bytes()), "sample.docx"),
                    "profile": "auto-premium",
                    "ocr": "false",
                    "language": "pl",
                    "heading_repair": "false",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"epub-docx")
        self.assertEqual(response.headers.get("X-Source-Type"), "docx")
        self.assertIsNone(response.headers.get("X-PDF-Type"))


if __name__ == "__main__":
    unittest.main()
