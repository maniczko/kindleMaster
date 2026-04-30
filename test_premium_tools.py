from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock, patch

import premium_tools


class PremiumToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        premium_tools.clear_toolchain_cache()
        premium_tools._EPUBCHECK_RESULT_CACHE.clear()

    def tearDown(self) -> None:
        premium_tools.clear_toolchain_cache()
        premium_tools._EPUBCHECK_RESULT_CACHE.clear()

    def test_run_epubcheck_caches_result_for_identical_epub_bytes(self) -> None:
        fake_toolchain = {
            "java": {"path": "java"},
            "epubcheck": {"jar_path": "epubcheck.jar"},
        }
        completed = subprocess.CompletedProcess(
            args=["java"],
            returncode=0,
            stdout="No errors or warnings detected.",
            stderr="",
        )

        with (
            patch("premium_tools.detect_toolchain", return_value=fake_toolchain),
            patch("premium_tools.subprocess.run", Mock(return_value=completed)) as run_mock,
        ):
            first = premium_tools.run_epubcheck(b"same epub")
            second = premium_tools.run_epubcheck(b"same epub")

        self.assertEqual(first["status"], "passed")
        self.assertEqual(second["status"], "passed")
        self.assertEqual(run_mock.call_count, 1)

    def test_detect_toolchain_refresh_prevents_stale_environment_cache(self) -> None:
        with patch("premium_tools._module_available", return_value=True):
            with patch("premium_tools.find_playwright_chromium_executable", return_value="C:/tools/chromium.exe"):
                supported = premium_tools.detect_toolchain(refresh=True)

        self.assertEqual(supported["verification_surfaces"]["browser"]["status"], "supported")

        with patch("premium_tools._module_available", return_value=True):
            with patch("premium_tools.find_playwright_chromium_executable", return_value=None):
                stale = premium_tools.detect_toolchain()
                refreshed = premium_tools.detect_toolchain(refresh=True)

        self.assertEqual(stale["verification_surfaces"]["browser"]["status"], "supported")
        self.assertEqual(refreshed["verification_surfaces"]["browser"]["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
