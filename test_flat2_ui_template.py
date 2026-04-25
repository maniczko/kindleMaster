from __future__ import annotations

import unittest
from pathlib import Path

from app import app


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"


class Flat2UiTemplateTests(unittest.TestCase):
    def test_index_renders_flat_shell_sidebar_and_quality_report_hooks(self) -> None:
        client = app.test_client()

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('class="flat-sidebar-card"', html)
        self.assertIn('id="quickUploadButton"', html)
        self.assertIn('id="recentConversionsList"', html)
        self.assertIn('class="flat2-quality-report"', html)
        self.assertIn('data-quality-verdict', html)

    def test_template_declares_flat2_visual_contract(self) -> None:
        html = TEMPLATE_PATH.read_text(encoding="utf-8")

        self.assertIn("Flat 2.0 visible contract", html)
        self.assertIn("--radius: 4px", html)
        self.assertIn("--shadow: none", html)
        self.assertIn(".quality-matrix", html)
        self.assertIn("Open quality JSON", html)


if __name__ == "__main__":
    unittest.main()
