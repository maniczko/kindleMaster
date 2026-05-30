from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import app as app_module
from app import app
from pdf_weight_reducer import PdfCompressionResult, PdfCompressionUnavailable


class AppPdfCompressionTests(unittest.TestCase):
    def setUp(self) -> None:
        app_module._PDF_COMPRESS_JOBS.clear()

    def tearDown(self) -> None:
        app_module._PDF_COMPRESS_JOBS.clear()

    def test_pdf_compress_rejects_docx_uploads(self) -> None:
        client = app.test_client()

        response = client.post(
            "/pdf/compress",
            data={"file": (BytesIO(b"docx"), "sample.docx"), "profile": "balanced"},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "pdf_compression_unsupported_source")

    def test_pdf_compress_returns_download_metadata_for_compressed_pdf(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_path = temp_path / "compressed.pdf"
            output_path.write_bytes(b"%PDF-1.4\ncompressed")
            with patch.object(app_module, "PDF_COMPRESS_DIR", temp_path):
                with patch.object(app_module, "_pdf_compression_source_warnings", return_value=[]):
                    with patch.object(
                        app_module,
                        "compress_pdf",
                        return_value=PdfCompressionResult(
                            success=True,
                            job_id="compress-job",
                            status="compressed",
                            original_path=str(temp_path / "source.pdf"),
                            output_path=str(output_path),
                            original_size_bytes=1000,
                            compressed_size_bytes=600,
                            reduction_percent=40.0,
                            quality_profile="balanced",
                            method="ghostscript+qpdf",
                            warnings=["review small text"],
                        ),
                    ):
                        response = client.post(
                            "/pdf/compress",
                            data={"file": (BytesIO(b"%PDF-1.4\n"), "sample.pdf"), "profile": "balanced"},
                            content_type="multipart/form-data",
                        )

            payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["job_id"], "compress-job")
        self.assertEqual(payload["download_url"], "/pdf/compress/download/compress-job")
        self.assertEqual(payload["reduction_percent"], 40.0)
        self.assertIn("review small text", payload["warnings"])

    def test_pdf_compress_download_returns_generated_pdf(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            output_path = temp_path / "compressed.pdf"
            output_path.write_bytes(b"%PDF-1.4\ncompressed")
            with patch.object(app_module, "PDF_COMPRESS_DIR", temp_path):
                app_module._PDF_COMPRESS_JOBS["compress-job"] = {
                    "job_id": "compress-job",
                    "output_path": str(output_path),
                    "download_name": "sample.compressed.pdf",
                    "created_at": app_module.datetime.now(app_module.UTC).isoformat().replace("+00:00", "Z"),
                }
                response = client.get("/pdf/compress/download/compress-job")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "application/pdf")
                self.assertIn("sample.compressed.pdf", response.headers["Content-Disposition"])
                self.assertEqual(response.data, b"%PDF-1.4\ncompressed")
                response.close()

    def test_pdf_compress_job_source_uses_server_side_artifact(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_path = temp_path / "source.pdf"
            output_path = temp_path / "compressed.pdf"
            source_path.write_bytes(b"%PDF-1.4\nsource")
            output_path.write_bytes(b"%PDF-1.4\ncompressed")
            job = {
                "job_id": "job-source",
                "source_type": "pdf",
                "filename": "source.pdf",
                "artifacts": {
                    "input": {
                        "filename": "source.pdf",
                        "signed_url": {"available": True, "url": "https://storage.example.com/signed-source.pdf"},
                    }
                },
            }
            with patch.object(app_module, "PDF_COMPRESS_DIR", temp_path):
                with patch.object(app_module, "_get_conversion_job", return_value=job):
                    with patch.object(app_module, "_resolve_local_artifact_path", return_value=source_path):
                        with patch.object(app_module, "_pdf_compression_source_warnings", return_value=[]):
                            with patch.object(
                                app_module,
                                "compress_pdf",
                                return_value=PdfCompressionResult(
                                    success=True,
                                    job_id="compress-from-job",
                                    status="compressed",
                                    original_path=str(source_path),
                                    output_path=str(output_path),
                                    original_size_bytes=1000,
                                    compressed_size_bytes=500,
                                    reduction_percent=50.0,
                                    quality_profile="balanced",
                                    method="ghostscript+qpdf",
                                    warnings=[],
                                ),
                            ) as compress_mock:
                                response = client.post("/pdf/compress/job/job-source", data={"profile": "balanced"})

            payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["source_job_id"], "job-source")
        self.assertEqual(payload["download_url"], "/pdf/compress/download/compress-from-job")
        compress_mock.assert_called_once()
        self.assertEqual(Path(compress_mock.call_args.args[0]), source_path)

    def test_pdf_compress_job_source_falls_back_to_local_file_by_name_and_size(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fallback_root = temp_path / "downloads"
            fallback_root.mkdir()
            source_path = fallback_root / "source.pdf"
            output_path = temp_path / "compressed.pdf"
            source_path.write_bytes(b"%PDF-1.4\nsource")
            output_path.write_bytes(b"%PDF-1.4\ncompressed")
            job = {
                "job_id": "job-source",
                "source_type": "pdf",
                "filename": "source.pdf",
                "artifacts": {
                    "input": {
                        "filename": "source.pdf",
                        "size_bytes": source_path.stat().st_size,
                        "signed_url": {"available": True, "url": "https://storage.example.com/missing.pdf"},
                    }
                },
            }
            with patch.object(app_module, "PDF_COMPRESS_DIR", temp_path):
                with patch.object(app_module, "_get_conversion_job", return_value=job):
                    with patch.object(app_module, "_resolve_local_artifact_path", return_value=None):
                        with patch.object(app_module, "_pdf_source_fallback_roots", return_value=[fallback_root]):
                            with patch.object(app_module, "_download_remote_pdf_artifact") as download_mock:
                                with patch.object(app_module, "_pdf_compression_source_warnings", return_value=[]):
                                    with patch.object(
                                        app_module,
                                        "compress_pdf",
                                        return_value=PdfCompressionResult(
                                            success=True,
                                            job_id="compress-from-fallback",
                                            status="compressed",
                                            original_path=str(source_path),
                                            output_path=str(output_path),
                                            original_size_bytes=1000,
                                            compressed_size_bytes=500,
                                            reduction_percent=50.0,
                                            quality_profile="balanced",
                                            method="ghostscript+qpdf",
                                            warnings=[],
                                        ),
                                    ) as compress_mock:
                                        response = client.post("/pdf/compress/job/job-source", data={"profile": "balanced"})

            payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        download_mock.assert_not_called()
        compress_mock.assert_called_once()
        self.assertEqual(Path(compress_mock.call_args.args[0]), source_path.resolve())

    def test_pdf_compress_reports_unavailable_toolchain(self) -> None:
        client = app.test_client()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(app_module, "PDF_COMPRESS_DIR", Path(temp_dir)):
                with patch.object(app_module, "_pdf_compression_source_warnings", return_value=[]):
                    with patch.object(app_module, "compress_pdf", side_effect=PdfCompressionUnavailable("PDF compression requires: qpdf.")):
                        response = client.post(
                            "/pdf/compress",
                            data={"file": (BytesIO(b"%PDF-1.4\n"), "sample.pdf"), "profile": "balanced"},
                            content_type="multipart/form-data",
                        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error_code"], "pdf_compression_unavailable")


if __name__ == "__main__":
    unittest.main()
