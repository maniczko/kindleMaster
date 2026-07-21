import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocr_module import OCRMY_PDF_TEMP_PREFIX, OCRPageResult, OCRResult, _ocr_cache_path, clear_ocr_cache, run_ocr_on_pdf


class OCRModuleCacheTests(unittest.TestCase):
    def test_run_ocr_on_pdf_reuses_disk_cache_for_same_source_and_mtime(self) -> None:
        class FakePage:
            pass

        class FakeDoc:
            def __len__(self) -> int:
                return 2

            def __getitem__(self, index: int) -> FakePage:
                return FakePage()

            def close(self) -> None:
                pass

        page_calls: list[int] = []

        def fake_run_ocr_on_page(_page, *, page_num: int, language: str, dpi: int, engine: str) -> OCRPageResult:
            page_calls.append(page_num)
            return OCRPageResult(
                page_num=page_num,
                text=f"page-{page_num}",
                confidence=0.91,
                image_data=f"jpeg-{page_num}".encode("utf-8"),
                image_width=120,
                image_height=160,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "scan.pdf"
            cache_root = root / "ocr-cache"
            pdf_path.write_bytes(b"%PDF-1.4\ncache-probe")
            clear_ocr_cache(root=cache_root)

            with patch.dict(os.environ, {"KINDLEMASTER_OCR_CACHE_ROOT": str(cache_root)}, clear=False), patch(
                "ocr_module.resolve_ocr_language",
                return_value="eng",
            ), patch("ocr_module.ocr_pdf_with_ocrmypdf", return_value=None), patch(
                "ocr_module.get_best_available_engine",
                return_value="tesseract",
            ), patch("ocr_module.run_ocr_on_page", side_effect=fake_run_ocr_on_page), patch(
                "ocr_module.fitz.open",
                side_effect=lambda *_args, **_kwargs: FakeDoc(),
            ):
                first = run_ocr_on_pdf(str(pdf_path), language="eng", dpi=300)
                second = run_ocr_on_pdf(str(pdf_path), language="eng", dpi=300)
                self.assertTrue(_ocr_cache_path(str(pdf_path), language="eng", dpi=300).exists())

        self.assertEqual(page_calls, [0, 1])
        self.assertEqual(first.engine_used, "tesseract")
        self.assertEqual(second.engine_used, "tesseract")
        self.assertEqual([page.text for page in second.pages], ["page-0", "page-1"])
        self.assertEqual(first.cache_status, "miss")
        self.assertGreaterEqual(first.backend_seconds, 0.0)
        self.assertEqual(second.cache_status, "hit")
        self.assertEqual(second.backend_seconds, 0.0)
        self.assertGreaterEqual(second.cache_lookup_seconds, 0.0)
        self.assertGreaterEqual(second.total_seconds, second.cache_lookup_seconds)

    def test_run_ocr_on_pdf_invalidates_cache_when_source_mtime_changes(self) -> None:
        class FakePage:
            pass

        class FakeDoc:
            def __len__(self) -> int:
                return 1

            def __getitem__(self, index: int) -> FakePage:
                return FakePage()

            def close(self) -> None:
                pass

        page_calls: list[int] = []

        def fake_run_ocr_on_page(_page, *, page_num: int, language: str, dpi: int, engine: str) -> OCRPageResult:
            page_calls.append(page_num)
            return OCRPageResult(
                page_num=page_num,
                text=f"page-{len(page_calls)}",
                confidence=0.88,
                image_data=f"jpeg-{len(page_calls)}".encode("utf-8"),
                image_width=100,
                image_height=140,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "scan.pdf"
            cache_root = root / "ocr-cache"
            pdf_path.write_bytes(b"%PDF-1.4\ncache-invalidate")
            clear_ocr_cache(root=cache_root)

            with patch.dict(os.environ, {"KINDLEMASTER_OCR_CACHE_ROOT": str(cache_root)}, clear=False), patch(
                "ocr_module.resolve_ocr_language",
                return_value="eng",
            ), patch("ocr_module.ocr_pdf_with_ocrmypdf", return_value=None), patch(
                "ocr_module.get_best_available_engine",
                return_value="tesseract",
            ), patch("ocr_module.run_ocr_on_page", side_effect=fake_run_ocr_on_page), patch(
                "ocr_module.fitz.open",
                side_effect=lambda *_args, **_kwargs: FakeDoc(),
            ):
                first = run_ocr_on_pdf(str(pdf_path), language="eng", dpi=300)
                stat = pdf_path.stat()
                os.utime(pdf_path, ns=(stat.st_atime_ns + 1_000_000, stat.st_mtime_ns + 1_000_000))
                second = run_ocr_on_pdf(str(pdf_path), language="eng", dpi=300)

        self.assertEqual(page_calls, [0, 0])
        self.assertEqual(first.pages[0].text, "page-1")
        self.assertEqual(second.pages[0].text, "page-2")

    def test_run_ocr_on_pdf_cleans_only_kindlemaster_temp_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "scan.pdf"
            cache_root = root / "ocr-cache"
            safe_output_dir = root / f"{OCRMY_PDF_TEMP_PREFIX}probe"
            safe_output_dir.mkdir()
            ocrmypdf_output = safe_output_dir / "ocr_output.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nsource")
            ocrmypdf_output.write_bytes(b"%PDF-1.4\nocr")
            clear_ocr_cache(root=cache_root)

            result = OCRResult(
                pages=[
                    OCRPageResult(
                        page_num=0,
                        text="cached",
                        confidence=0.95,
                        image_data=b"jpeg-0",
                        image_width=120,
                        image_height=160,
                    )
                ],
                engine_used="ocrmypdf",
                total_pages=1,
                success_rate=1.0,
            )

            with patch.dict(os.environ, {"KINDLEMASTER_OCR_CACHE_ROOT": str(cache_root)}, clear=False), patch(
                "ocr_module._ocr_result_from_pdf_text",
                return_value=result,
            ), patch("ocr_module.ocr_pdf_with_ocrmypdf", return_value=ocrmypdf_output):
                run_ocr_on_pdf(str(pdf_path), language="eng", dpi=300)

            self.assertTrue(pdf_path.exists())
            self.assertFalse(safe_output_dir.exists())


if __name__ == "__main__":
    unittest.main()
