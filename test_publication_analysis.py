from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

from premium_reflow import _build_chapter_drafts, _select_positioned_outline_chapter_entries
from publication_analysis import _choose_profile, analyze_publication
from publication_pipeline import _normalize_section_title_candidate


class PublicationAnalysisTests(unittest.TestCase):
    def test_forced_diagram_profile_skips_expensive_table_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "diagram-profile.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "White to move. " * 20)
            doc.save(str(pdf_path))
            doc.close()

            with (
                patch("publication_analysis._detect_tables", side_effect=AssertionError("table scan should be skipped")),
                patch("publication_analysis.detect_toolchain", return_value={}),
            ):
                analysis = analyze_publication(str(pdf_path), preferred_profile="diagram_book_reflow")

        self.assertEqual(analysis.profile, "diagram_book_reflow")
        self.assertFalse(analysis.has_tables)

    def test_document_like_report_routes_to_technical_book_before_magazine(self) -> None:
        profile, ui_profile, reason = _choose_profile(
            preferred_profile="auto-premium",
            total_pages=20,
            has_toc=True,
            has_tables=True,
            has_diagrams=False,
            has_meaningful_images=True,
            estimated_columns=1,
            layout_heavy=True,
            text_heavy=True,
            text_page_ratio=0.95,
            scanned_page_ratio=0.0,
            legacy_strategy="layout_fixed",
        )

        self.assertEqual(profile, "book_reflow")
        self.assertEqual(ui_profile, "technical-study")
        self.assertIn("raport", reason.lower())

    def test_section_title_normalization_preserves_meaningful_hyphenated_report_titles(self) -> None:
        self.assertEqual(
            _normalize_section_title_candidate("3. Proces płatności kartowej krok po kroku - bardzo szczegółowo"),
            "3. Proces płatności kartowej krok po kroku - bardzo szczegółowo",
        )
        self.assertEqual(
            _normalize_section_title_candidate("11. Model operatingowy i ownership - kto naprawdę odpowiada za wynik cash flow"),
            "11. Model operatingowy i ownership - kto naprawdę odpowiada za wynik cash flow",
        )

    def test_positioned_outline_keeps_multiple_top_level_sections_on_same_page(self) -> None:
        entries = [
            {"level": 1, "title": "8. Najczęstsze ryzyka", "page": 12, "y": 349.0},
            {"level": 1, "title": "9. Scenariusze UAT", "page": 12, "y": 594.0},
            {"level": 2, "title": "9.1. Szczegół", "page": 12, "y": 630.0},
            {"level": 1, "title": "10. Backlog", "page": 14, "y": 119.0},
        ]

        selected = _select_positioned_outline_chapter_entries(entries)

        self.assertEqual([entry["title"] for entry in selected], ["8. Najczęstsze ryzyka", "9. Scenariusze UAT", "10. Backlog"])

    def test_positioned_chapter_drafts_use_y_boundaries_for_same_page_sections(self) -> None:
        class FakeDoc:
            def __len__(self) -> int:
                return 15

            def get_toc(self, simple: bool = True):
                return [
                    [1, "8. Najczęstsze ryzyka", 13, {"to": SimpleNamespace(y=349.0)}],
                    [1, "9. Scenariusze UAT", 13, {"to": SimpleNamespace(y=594.0)}],
                    [1, "10. Backlog", 15, {"to": SimpleNamespace(y=119.0)}],
                ]

        drafts = _build_chapter_drafts(FakeDoc(), [], use_outline_positions=True)

        self.assertEqual([draft.title for draft in drafts], ["Front Matter", "8. Najczęstsze ryzyka", "9. Scenariusze UAT", "10. Backlog"])
        self.assertEqual((drafts[1].page_start, drafts[1].page_end, drafts[1].y_start, drafts[1].y_end), (12, 12, 349.0, 594.0))
        self.assertEqual((drafts[2].page_start, drafts[2].page_end, drafts[2].y_start, drafts[2].y_end), (12, 14, 594.0, 119.0))


if __name__ == "__main__":
    unittest.main()
