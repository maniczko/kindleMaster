from __future__ import annotations

import subprocess
import tempfile
import unittest
from unittest.mock import patch

from premium_tools import clear_epubcheck_cache, run_epubcheck


class PremiumToolsTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_epubcheck_cache()

    def test_run_epubcheck_cache_is_keyed_by_epub_bytes_and_returns_copy(self) -> None:
        clear_epubcheck_cache()
        completed = subprocess.CompletedProcess(
            args=["java", "-jar", "epubcheck.jar", "validation.epub"],
            returncode=0,
            stdout="No errors or warnings detected.",
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "premium_tools.detect_toolchain",
                return_value={"java": {"path": "java"}, "epubcheck": {"jar_path": f"{temp_dir}/epubcheck.jar"}},
            ), patch("premium_tools.subprocess.run", return_value=completed) as mock_run:
                first = run_epubcheck(b"epub-a")
                first["status"] = "mutated"
                second = run_epubcheck(b"epub-a")
                third = run_epubcheck(b"epub-b")

        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(second["status"], "passed")
        self.assertEqual(third["status"], "passed")


if __name__ == "__main__":
    unittest.main()
