from __future__ import annotations

import unittest

from publication_model import PublicationAnalysis
from publication_pipeline import publication_from_content


class PublicationPipelineTests(unittest.TestCase):
    def test_diagram_book_back_cover_is_not_flagged_as_empty_fallback_section(self) -> None:
        analysis = PublicationAnalysis(
            profile="diagram_book_reflow",
            confidence=0.91,
            page_count=394,
            render_budget_class="",
            has_toc=True,
            has_tables=False,
            has_diagrams=True,
            has_meaningful_images=True,
            estimated_sections=18,
            fallback_recommendation="",
            ui_profile="book",
            legacy_strategy="image-first-reflow",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=False,
        )
        content = {
            "images": [],
            "chapters": [
                {
                    "title": "Back Cover",
                    "html_parts": [],
                    "images": [],
                    "_page_start": 393,
                    "_page_end": 393,
                }
            ],
            "audit": {},
        }

        document = publication_from_content(
            content,
            analysis,
            title="The Woodpecker Method",
            author="Axel Smith",
            language="en",
        )

        self.assertEqual(document.sections[0].kind, "appendix")
        self.assertEqual(document.quality_report.fallback_pages, [])
        self.assertEqual(document.quality_report.fallback_sections, [])
        self.assertEqual(document.quality_report.fallback_regions, [])
        self.assertNotIn(
            "Wykryto 1 pustych lub fallbackowych sekcji.",
            document.quality_report.warnings,
        )
