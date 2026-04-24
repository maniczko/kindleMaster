from __future__ import annotations

import unittest
from unittest.mock import patch

from converter import ConversionConfig, SizeBudgetExceededError, _build_fixed_layout_epub_with_budget


class FixedLayoutBudgetEnforcementTests(unittest.TestCase):
    def test_fixed_layout_budget_retries_with_fallback_before_success(self) -> None:
        attempts: list[str] = []

        def fake_build(_pdf_path, config, _pdf_metadata):
            attempts.append(config.render_budget_attempt)
            return (f"epub-{config.render_budget_attempt}".encode("utf-8"), "fixed_layout_v2")

        size_gate_results = [
            {
                "status": "failed",
                "message": "primary too large",
                "warn_bytes": 20,
                "hard_bytes": 40,
                "inspection": {"entry_count": 1, "image_count": 0, "largest_assets": []},
            },
            {
                "status": "passed",
                "message": "fallback ok",
                "warn_bytes": 20,
                "hard_bytes": 40,
                "inspection": {"entry_count": 1, "image_count": 0, "largest_assets": []},
            },
        ]

        with patch("converter._build_fixed_layout_epub_once", side_effect=fake_build), patch(
            "converter.finalize_epub_bytes",
            side_effect=lambda epub_bytes, *_args, **_kwargs: epub_bytes,
        ), patch(
            "converter.inspect_epub_archive",
            return_value={"entry_count": 1, "image_count": 0, "largest_assets": []},
        ), patch("converter.evaluate_size_budget", side_effect=size_gate_results):
            epub_bytes, details = _build_fixed_layout_epub_with_budget(
                "dummy.pdf",
                config=ConversionConfig(),
                pdf_metadata={"title": "Dummy"},
                original_filename="dummy.pdf",
                render_budget_class="fixed_layout_dense",
            )

        self.assertEqual(attempts, ["primary", "fallback"])
        self.assertEqual(epub_bytes, b"epub-fallback")
        self.assertEqual(details["render_budget_attempt"], "fallback")
        self.assertEqual(details["size_budget_status"], "passed")
        self.assertEqual(details["builder"], "fixed_layout_v2")

    def test_fixed_layout_budget_raises_controlled_error_after_failed_fallback(self) -> None:
        attempts: list[str] = []

        def fake_build(_pdf_path, config, _pdf_metadata):
            attempts.append(config.render_budget_attempt)
            return (f"epub-{config.render_budget_attempt}".encode("utf-8"), "fixed_layout_v1")

        size_gate_results = [
            {
                "status": "failed",
                "message": "primary too large",
                "warn_bytes": 20,
                "hard_bytes": 40,
                "inspection": {"entry_count": 1, "image_count": 0, "largest_assets": []},
            },
            {
                "status": "failed",
                "message": "fallback still too large",
                "warn_bytes": 20,
                "hard_bytes": 40,
                "inspection": {"entry_count": 1, "image_count": 0, "largest_assets": []},
            },
        ]

        with patch("converter._build_fixed_layout_epub_once", side_effect=fake_build), patch(
            "converter.finalize_epub_bytes",
            side_effect=lambda epub_bytes, *_args, **_kwargs: epub_bytes,
        ), patch(
            "converter.inspect_epub_archive",
            return_value={"entry_count": 1, "image_count": 0, "largest_assets": []},
        ), patch("converter.evaluate_size_budget", side_effect=size_gate_results):
            with self.assertRaises(SizeBudgetExceededError) as error_context:
                _build_fixed_layout_epub_with_budget(
                    "dummy.pdf",
                    config=ConversionConfig(),
                    pdf_metadata={"title": "Dummy"},
                    original_filename="dummy.pdf",
                    render_budget_class="fixed_layout_dense",
                )

        self.assertEqual(attempts, ["primary", "fallback"])
        payload = error_context.exception.payload
        self.assertEqual(payload["error_code"], "size_budget_exceeded")
        self.assertEqual(payload["render_budget_attempt"], "fallback")
        self.assertEqual(payload["size_budget_status"], "failed")
        self.assertIn("fallback still too large", payload["size_budget_message"])


if __name__ == "__main__":
    unittest.main()
