from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from pdf_weight_reducer import (
    PdfCompressionFailed,
    PdfCompressionResult,
    PdfCompressionUnavailable,
    _ghostscript_command,
    compress_pdf,
    normalize_compression_profile,
)


def _pdf_bytes(*, trailing_bytes: int = 0) -> bytes:
    document = fitz.open()
    document.new_page(width=120, height=120)
    data = document.tobytes()
    document.close()
    return data + (b"\n%" + (b"x" * trailing_bytes) if trailing_bytes else b"")


class PdfWeightReducerTests(unittest.TestCase):
    def test_normalize_compression_profile_defaults_to_balanced(self) -> None:
        self.assertEqual(normalize_compression_profile("safe"), "safe")
        self.assertEqual(normalize_compression_profile("aggressive"), "aggressive")
        self.assertEqual(normalize_compression_profile("unknown"), "balanced")
        self.assertEqual(normalize_compression_profile(None), "balanced")

    def test_profile_builds_expected_ghostscript_downsample_parameters(self) -> None:
        command = _ghostscript_command("gs", Path("input.pdf"), Path("output.pdf"), profile=__import__("pdf_weight_reducer").COMPRESSION_PROFILES["aggressive"])

        self.assertIn("-dColorImageResolution=110", command)
        self.assertIn("-dGrayImageResolution=110", command)
        self.assertIn("-dMonoImageResolution=220", command)
        self.assertIn("-dJPEGQ=66", command)

    def test_compress_pdf_returns_smaller_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "source.pdf"
            source.write_bytes(_pdf_bytes(trailing_bytes=5000))

            def runner(command, **kwargs):
                del kwargs
                if any(str(item).startswith("-sOutputFile=") for item in command):
                    output = Path(next(str(item).split("=", 1)[1] for item in command if str(item).startswith("-sOutputFile=")))
                    output.write_bytes(_pdf_bytes(trailing_bytes=200))
                else:
                    Path(command[-1]).write_bytes(_pdf_bytes())
                return subprocess.CompletedProcess(command, 0)

            result = compress_pdf(
                source,
                temp_path / "out",
                profile="balanced",
                ghostscript_path="gs",
                qpdf_path="qpdf",
                runner=runner,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "compressed")
        self.assertGreater(result.reduction_percent, 0)
        self.assertTrue(Path(result.output_path).name.endswith(".pdf"))

    def test_compress_pdf_blocks_output_when_size_does_not_decrease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "source.pdf"
            source.write_bytes(_pdf_bytes())

            def runner(command, **kwargs):
                del kwargs
                if any(str(item).startswith("-sOutputFile=") for item in command):
                    output = Path(next(str(item).split("=", 1)[1] for item in command if str(item).startswith("-sOutputFile=")))
                    output.write_bytes(_pdf_bytes(trailing_bytes=100))
                else:
                    Path(command[-1]).write_bytes(_pdf_bytes(trailing_bytes=1000))
                return subprocess.CompletedProcess(command, 0)

            result = compress_pdf(
                source,
                temp_path / "out",
                profile="safe",
                ghostscript_path="gs",
                qpdf_path="qpdf",
                runner=runner,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "no_reduction")
        self.assertEqual(result.output_path, "")

    def test_compress_pdf_requires_ghostscript_and_qpdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            source.write_bytes(_pdf_bytes())
            with patch("pdf_weight_reducer.find_ghostscript_executable", return_value=None):
                with patch("pdf_weight_reducer.find_qpdf_executable", return_value=None):
                    with self.assertRaises(PdfCompressionUnavailable):
                        compress_pdf(source, Path(temp_dir) / "out")

    def test_compress_pdf_blocks_page_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "source.pdf"
            source.write_bytes(_pdf_bytes(trailing_bytes=1000))

            def runner(command, **kwargs):
                del kwargs
                target = Path(command[-1]) if not any(str(item).startswith("-sOutputFile=") for item in command) else Path(next(str(item).split("=", 1)[1] for item in command if str(item).startswith("-sOutputFile=")))
                target.write_bytes(_pdf_bytes())
                return subprocess.CompletedProcess(command, 0)

            with patch("pdf_weight_reducer._page_count", side_effect=[2, 1]):
                with self.assertRaises(PdfCompressionFailed):
                    compress_pdf(
                        source,
                        temp_path / "out",
                        ghostscript_path="gs",
                        qpdf_path="qpdf",
                        runner=runner,
                    )


if __name__ == "__main__":
    unittest.main()
