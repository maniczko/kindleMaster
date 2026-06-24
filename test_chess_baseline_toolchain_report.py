import unittest

from scripts.generate_chess_baseline_toolchain_report import build_chess_baseline_toolchain_reports


class ChessBaselineToolchainReportTests(unittest.TestCase):
    def test_dependency_gap_report_flags_missing_ocr_and_diagnostic_dependencies(self):
        baseline, gaps = build_chess_baseline_toolchain_reports(
            {
                "python_modules": {
                    "chess": True,
                    "ocrmypdf": False,
                    "pytesseract": False,
                    "cv2": False,
                },
                "commands": {
                    "tesseract": False,
                    "ocrmypdf": False,
                    "qpdf": True,
                    "ghostscript": True,
                },
                "verification_surfaces": {
                    "quick": {"status": "supported", "missing_requirements": []},
                    "corpus": {"status": "supported", "missing_requirements": []},
                },
                "conversion_capabilities": {
                    "ocr_pipeline": {"status": "unavailable", "missing_requirements": ["Tesseract OCR executable"]},
                    "core_conversion": {"status": "supported", "missing_requirements": []},
                },
            },
            generated_at="2026-06-20T00:00:00+00:00",
        )

        self.assertEqual(baseline["statuses"]["ocr"], "unavailable")
        self.assertEqual(baseline["statuses"]["crop_grid_diagnostics"], "degraded")
        self.assertEqual(baseline["dependency_decisions"]["pytesseract"]["support_level"], "runtime")
        self.assertEqual(baseline["dependency_decisions"]["opencv-python-headless"]["support_level"], "developer_diagnostic")
        dependencies = {gap["dependency"] for gap in gaps["gaps"]}
        self.assertIn("pytesseract>=0.3.13", dependencies)
        self.assertIn("Tesseract OCR executable", dependencies)
        self.assertIn("opencv-python-headless>=4.10.0", dependencies)

    def test_missing_python_chess_marks_fen_and_pgn_unavailable(self):
        baseline, gaps = build_chess_baseline_toolchain_reports(
            {
                "python_modules": {
                    "chess": False,
                    "ocrmypdf": True,
                    "pytesseract": True,
                    "cv2": True,
                },
                "commands": {
                    "tesseract": True,
                    "ocrmypdf": True,
                    "qpdf": True,
                    "ghostscript": True,
                },
                "verification_surfaces": {},
                "conversion_capabilities": {},
            },
            generated_at="2026-06-20T00:00:00+00:00",
        )

        self.assertEqual(baseline["statuses"]["fen"], "unavailable")
        self.assertEqual(baseline["statuses"]["pgn"], "unavailable")
        dependencies = {gap["dependency"] for gap in gaps["gaps"]}
        self.assertIn("chess>=1.11,<2", dependencies)

    def test_report_is_ok_when_chess_ocr_and_diagnostics_are_available(self):
        baseline, gaps = build_chess_baseline_toolchain_reports(
            {
                "python_modules": {
                    "chess": True,
                    "ocrmypdf": True,
                    "pytesseract": True,
                    "cv2": True,
                },
                "commands": {
                    "tesseract": True,
                    "ocrmypdf": True,
                    "qpdf": True,
                    "ghostscript": True,
                },
                "verification_surfaces": {},
                "conversion_capabilities": {},
            },
            generated_at="2026-06-20T00:00:00+00:00",
        )

        self.assertEqual(baseline["statuses"]["ocr"], "ok")
        self.assertEqual(baseline["statuses"]["fen"], "ok")
        self.assertEqual(baseline["statuses"]["pgn"], "ok")
        self.assertEqual(baseline["statuses"]["crop_grid_diagnostics"], "ok")
        self.assertEqual(gaps["status"], "ok")
        self.assertEqual(gaps["gaps"], [])


if __name__ == "__main__":
    unittest.main()
