from __future__ import annotations

import json
import unittest
from pathlib import Path

from fixed_layout_builder_v2 import resolve_fixed_layout_render_settings
from publication_analysis import _choose_render_budget_class
from size_budget_policy import evaluate_size_budget, load_size_budget_policy


class FixedLayoutRenderBudgetTests(unittest.TestCase):
    def test_choose_render_budget_class_prefers_extreme_for_large_scanned_documents(self) -> None:
        budget_class = _choose_render_budget_class(
            total_pages=420,
            scanned_page_ratio=0.82,
            has_diagrams=False,
            has_meaningful_images=True,
            layout_heavy=True,
            estimated_columns=1,
        )

        self.assertEqual(budget_class, "fixed_layout_extreme")

    def test_choose_render_budget_class_returns_balanced_for_small_visual_documents(self) -> None:
        budget_class = _choose_render_budget_class(
            total_pages=72,
            scanned_page_ratio=0.05,
            has_diagrams=False,
            has_meaningful_images=True,
            layout_heavy=True,
            estimated_columns=1,
        )

        self.assertEqual(budget_class, "fixed_layout_balanced")

    def test_policy_driven_render_settings_use_primary_and_fallback_presets(self) -> None:
        primary = resolve_fixed_layout_render_settings(999, render_budget_class="fixed_layout_dense", attempt="primary")
        fallback = resolve_fixed_layout_render_settings(999, render_budget_class="fixed_layout_dense", attempt="fallback")

        self.assertEqual((primary.dpi, primary.jpeg_quality, primary.jpeg_subsampling, primary.cover_dpi, primary.cover_quality), (150, 78, 2, 120, 80))
        self.assertEqual((fallback.dpi, fallback.jpeg_quality, fallback.jpeg_subsampling, fallback.cover_dpi, fallback.cover_quality), (142, 75, 2, 112, 78))

    def test_page_count_heuristics_still_work_without_render_budget_class(self) -> None:
        short = resolve_fixed_layout_render_settings(48)
        medium = resolve_fixed_layout_render_settings(180)
        large = resolve_fixed_layout_render_settings(280)
        huge = resolve_fixed_layout_render_settings(420)

        self.assertEqual((short.dpi, short.jpeg_quality, short.jpeg_subsampling, short.cover_dpi), (180, 85, 1, 150))
        self.assertLess(medium.dpi, short.dpi)
        self.assertLess(large.dpi, medium.dpi)
        self.assertLess(huge.dpi, large.dpi)

    def test_size_budget_policy_covers_manifest_document_classes(self) -> None:
        policy = load_size_budget_policy()
        manifest = json.loads(Path("reference_inputs/manifest.json").read_text(encoding="utf-8"))
        missing = []
        for case in manifest.get("cases", []):
            budget_key = str(case.get("document_class", "")).replace("-", "_")
            if budget_key not in policy["document_classes"]:
                missing.append(case.get("document_class"))

        self.assertEqual(missing, [], f"Brak budzetow dla klas manifestu: {missing}")

    def test_size_budget_evaluation_fails_without_declared_class_budget(self) -> None:
        payload = evaluate_size_budget(
            budget_key="unknown_class",
            budget=None,
            epub_size_bytes=4096,
            inspection={"entry_count": 3, "image_count": 0, "largest_assets": []},
            label="klasy dokumentu",
        )

        self.assertEqual(payload["status"], "failed")
        self.assertIn("Brak zdefiniowanego budzetu", payload["message"])


if __name__ == "__main__":
    unittest.main()
