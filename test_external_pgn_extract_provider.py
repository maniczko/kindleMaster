from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from external_pgn_extract_provider import probe_pgn_extract_tool, run_pgn_extract


class ExternalPgnExtractProviderTests(unittest.TestCase):
    def test_missing_binary_returns_unavailable(self) -> None:
        with patch("external_pgn_extract_provider.shutil.which", return_value=None):
            result = run_pgn_extract("1. e4 e5 *", tool_path="pgn-extract")

        self.assertFalse(result.available)
        self.assertIn("pgn_extract_unavailable", result.warnings)

    def test_timeout_returns_warning(self) -> None:
        with patch("external_pgn_extract_provider.shutil.which", return_value="pgn-extract"):
            with patch(
                "external_pgn_extract_provider.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["pgn-extract"], timeout=0.01),
            ):
                result = run_pgn_extract("1. e4 e5 *", tool_path="pgn-extract", timeout_ms=250)

        self.assertTrue(result.available)
        self.assertIn("pgn_extract_timeout", result.warnings)

    def test_successful_subprocess_normalizes_stdout_and_stderr(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["pgn-extract", "input.pgn"],
            returncode=0,
            stdout='[Event "?"]\n\n1. e4 e5 *\n',
            stderr="warning text",
        )
        with patch("external_pgn_extract_provider.shutil.which", return_value="pgn-extract"):
            with patch("external_pgn_extract_provider.subprocess.run", return_value=completed):
                result = run_pgn_extract("1. e4 e5 *", tool_path="pgn-extract")

        self.assertTrue(result.available)
        self.assertEqual(result.returncode, 0)
        self.assertIn("1. e4 e5", result.stdout_pgn)
        self.assertEqual(result.stderr, "warning text")
        self.assertIn("pgn_extract_available", result.warnings)

    def test_nonzero_exit_does_not_crash(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["pgn-extract", "input.pgn"],
            returncode=3,
            stdout="",
            stderr="parse failed",
        )
        with patch("external_pgn_extract_provider.shutil.which", return_value="pgn-extract"):
            with patch("external_pgn_extract_provider.subprocess.run", return_value=completed):
                result = run_pgn_extract("bad", tool_path="pgn-extract")

        self.assertTrue(result.available)
        self.assertEqual(result.returncode, 3)
        self.assertIn("pgn_extract_nonzero_exit", result.warnings)

    def test_probe_uses_version_or_help(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["pgn-extract", "--version"],
            returncode=0,
            stdout="pgn-extract version x\n",
            stderr="",
        )
        with patch("external_pgn_extract_provider.shutil.which", return_value="pgn-extract"):
            with patch("external_pgn_extract_provider.subprocess.run", return_value=completed):
                result = probe_pgn_extract_tool(tool_path="pgn-extract")

        self.assertTrue(result.available)
        self.assertEqual(result.tool_version, "pgn-extract version x")


if __name__ == "__main__":
    unittest.main()
