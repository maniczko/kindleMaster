from __future__ import annotations

import io
import json
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

import fitz

from PIL import Image

from fixed_layout_builder_v2 import (
    demote_fixed_layout_non_content_pages,
    inject_fixed_layout_viewports,
    render_page_to_image,
    repair_fixed_layout_epub,
    repair_fixed_layout_epub_package,
    resolve_fixed_layout_render_settings,
)
from publication_analysis import _choose_render_budget_class
from size_budget_policy import evaluate_size_budget, load_size_budget_policy


class FixedLayoutRenderBudgetTests(unittest.TestCase):
    def test_fixed_layout_package_repair_adds_viewport_to_nav_and_normalizes_uuid(self) -> None:
        source = io.BytesIO()
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr(
                "EPUB/content.opf",
                '<package><metadata><dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/">'
                "urn:uuid:6b6971a34a2e4150b7e68e915a3fddab"
                "</dc:identifier></metadata></package>",
            )
            archive.writestr(
                "EPUB/nav.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Nav</title></head><body/></html>',
            )
            archive.writestr(
                "EPUB/page_000.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Page</title></head><body/></html>',
            )

        repaired = repair_fixed_layout_epub_package(
            source.getvalue(),
            {"EPUB/page_000.xhtml": (612, 792)},
        )

        with zipfile.ZipFile(io.BytesIO(repaired)) as archive:
            opf = archive.read("EPUB/content.opf").decode("utf-8")
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
            page = archive.read("EPUB/page_000.xhtml").decode("utf-8")

        self.assertIn("urn:uuid:6b6971a3-4a2e-4150-b7e6-8e915a3fddab", opf)
        self.assertIn('name="viewport" content="width=612,height=792"', nav)
        self.assertIn('name="viewport" content="width=612,height=792"', page)

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

    def test_fixed_layout_viewport_injection_covers_nav_and_cover(self) -> None:
        epub_bytes = BytesIO()
        with zipfile.ZipFile(epub_bytes, "w") as archive:
            archive.writestr("EPUB/nav.xhtml", "<html><head><title>Nav</title></head><body/></html>")
            archive.writestr("EPUB/cover.xhtml", "<html><head><title>Cover</title></head><body/></html>")
            archive.writestr("EPUB/page_000.xhtml", "<html><head><title>Page</title></head><body/></html>")

        repaired = inject_fixed_layout_viewports(
            epub_bytes.getvalue(),
            {"EPUB/page_000.xhtml": (612, 792)},
        )

        with zipfile.ZipFile(BytesIO(repaired), "r") as archive:
            for name in ("EPUB/nav.xhtml", "EPUB/cover.xhtml", "EPUB/page_000.xhtml"):
                text = archive.read(name).decode("utf-8")
                self.assertIn('name="viewport"', text)
                self.assertIn("width=612,height=792", text)

    def test_fixed_layout_package_repair_canonicalizes_uuid_identifier(self) -> None:
        epub_bytes = BytesIO()
        with zipfile.ZipFile(epub_bytes, "w") as archive:
            archive.writestr(
                "EPUB/content.opf",
                """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:6a8c54c0781441a1948bd08d68ef99d9</dc:identifier>
  </metadata>
</package>""",
            )

        repaired = repair_fixed_layout_epub(epub_bytes.getvalue())

        with zipfile.ZipFile(BytesIO(repaired), "r") as archive:
            text = archive.read("EPUB/content.opf").decode("utf-8")
        self.assertIn("urn:uuid:6a8c54c0-7814-41a1-948b-d08d68ef99d9", text)

    def test_non_content_demotion_uses_linear_no_without_custom_opf_prefix(self) -> None:
        epub_bytes = BytesIO()
        with zipfile.ZipFile(epub_bytes, "w") as archive:
            archive.writestr(
                "EPUB/content.opf",
                """<package><metadata></metadata><manifest>
<item id="p0" href="page_000.xhtml" media-type="application/xhtml+xml"/>
</manifest><spine><itemref idref="p0"/></spine></package>""",
            )

        repaired = demote_fixed_layout_non_content_pages(epub_bytes.getvalue(), {"page_000.xhtml"})

        with zipfile.ZipFile(BytesIO(repaired), "r") as archive:
            text = archive.read("EPUB/content.opf").decode("utf-8")
        self.assertIn('linear="no"', text)
        self.assertNotIn("kindlemaster:", text)

    def test_render_page_to_image_emits_baseline_jpeg(self) -> None:
        doc = fitz.open()
        page = doc.new_page(width=120, height=120)
        page.insert_text((20, 60), "Baseline JPEG")
        image_bytes, _width, _height = render_page_to_image(page, dpi=72, jpeg_quality=75)
        doc.close()

        image = Image.open(BytesIO(image_bytes))

        self.assertEqual(image.format, "JPEG")
        self.assertFalse(bool(image.info.get("progression")))


if __name__ == "__main__":
    unittest.main()
