from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kindlemaster import _run_convert


class KindleMasterEntrypointTests(unittest.TestCase):
    def test_run_convert_writes_json_report_for_pdf(self) -> None:
        input_path = Path("reference_inputs/pdf/ocr_probe.pdf").resolve()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "probe.epub"
            report_path = Path(temp_dir) / "probe.json"
            exit_code = _run_convert(
                input_path=str(input_path),
                output_path=str(output_path),
                language="pl",
                profile="auto-premium",
                heading_repair=False,
                report_json=str(report_path),
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(report_path.exists())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_type"], "pdf")
            self.assertIn("quality_report", payload)
            self.assertIn("document_summary", payload)


if __name__ == "__main__":
    unittest.main()
