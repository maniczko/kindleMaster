from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

from premium_reflow import PublicationTable, _build_chapter_drafts, _publication_table_to_html, _select_positioned_outline_chapter_entries
from publication_analysis import _choose_profile, analyze_publication
from publication_pipeline import _normalize_section_title_candidate


class PublicationAnalysisTests(unittest.TestCase):
    def _create_numbered_training_pdf_without_bookmarks(self, pdf_path: Path) -> None:
        doc = fitz.open()
        for page_index in range(4):
            page = doc.new_page(width=595, height=842)
            page.insert_text((42, 32), f"Material do nauki - Coupa | Strona {page_index + 1}", fontsize=8)
            if page_index == 0:
                page.insert_text((72, 92), "Material do nauki i podniesienia wartosci rynkowej", fontsize=15)
                page.insert_text((72, 132), "1. Co ta oferta naprawde premiuje", fontsize=16)
            elif page_index == 1:
                page.insert_text((72, 92), "2. Jak ta rola rozklada sie na realne zadania", fontsize=16)
            elif page_index == 2:
                page.insert_text((72, 92), "3.2 Jak myslec o e-invoicing na poziomie PMO", fontsize=15)
            else:
                page.insert_text((72, 92), "18.1 Case A - dostawca nie moze wystawic faktury", fontsize=15)
            page.insert_text(
                (72, 132),
                "Ten akapit opisuje proces, governance, ownerow, ryzyka i decyzje. " * 4,
                fontsize=10,
            )
            page.draw_rect(fitz.Rect(72, 240, 520, 330))
            page.draw_line((72, 270), (520, 270))
            page.draw_line((220, 240), (220, 330))
            page.draw_line((370, 240), (370, 330))
            page.insert_text((82, 258), "Tabela 1. Priorytety kompetencyjne", fontsize=9)
            page.insert_text((82, 290), "Obszar", fontsize=9)
            page.insert_text((230, 290), "Waga", fontsize=9)
            page.insert_text((380, 290), "Priorytet", fontsize=9)
            page.draw_rect(fitz.Rect(72, 420, 240, 510))
            page.insert_text((82, 462), "Rys. 1", fontsize=9)
        doc.save(pdf_path)
        doc.close()

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
        self.assertEqual(analysis.route_decision["mode"], "shadow")
        self.assertEqual(analysis.route_decision["selected_profile"], "diagram_book_reflow")

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

    def test_numbered_training_report_without_bookmarks_routes_to_technical_book(self) -> None:
        profile, ui_profile, reason = _choose_profile(
            preferred_profile="auto-premium",
            total_pages=20,
            has_toc=False,
            has_tables=True,
            has_diagrams=False,
            has_meaningful_images=True,
            estimated_columns=2,
            layout_heavy=True,
            text_heavy=True,
            text_page_ratio=1.0,
            scanned_page_ratio=0.0,
            legacy_strategy="layout_fixed",
            numbered_section_count=18,
        )

        self.assertEqual(profile, "book_reflow")
        self.assertEqual(ui_profile, "technical-study")
        self.assertIn("numerowane", reason.lower())

    def test_analyze_publication_detects_numbered_report_outline_without_pdf_bookmarks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "material_nauka_coupa_iwo_v5.pdf"
            self._create_numbered_training_pdf_without_bookmarks(pdf_path)

            with patch("publication_analysis.detect_toolchain", return_value={}):
                analysis = analyze_publication(str(pdf_path))

        self.assertFalse(analysis.has_toc)
        self.assertEqual(analysis.profile, "book_reflow")
        self.assertEqual(analysis.ui_profile, "technical-study")
        self.assertIn("numbered-sections", analysis.detected_features)
        self.assertGreaterEqual(analysis.estimated_sections, 4)

    def test_premium_reflow_builds_synthetic_chapter_drafts_from_numbered_headings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "material_nauka_coupa_iwo_v5.pdf"
            self._create_numbered_training_pdf_without_bookmarks(pdf_path)
            doc = fitz.open(pdf_path)
            try:
                drafts = _build_chapter_drafts(doc, [])
            finally:
                doc.close()

        titles = [draft.title for draft in drafts]
        self.assertIn("1. Co ta oferta naprawde premiuje", titles)
        self.assertIn("2. Jak ta rola rozklada sie na realne zadania", titles)
        self.assertIn("3.2 Jak myslec o e-invoicing na poziomie PMO", titles)
        self.assertIn("18.1 Case A - dostawca nie moze wystawic faktury", titles)

    def test_captioned_table_candidates_render_as_kindle_table_or_row_list(self) -> None:
        table = PublicationTable(
            page_index=0,
            bbox=(72.0, 240.0, 520.0, 330.0),
            y_position=240.0,
            rows=[
                ["Obszar", "Waga", "Priorytet"],
                ["E-invoicing", "5.0", "wysoki"],
                ["PMO governance", "4.9", "wysoki"],
            ],
            header_rows=1,
            caption="Tabela 1. Priorytety kompetencyjne",
            confidence=0.96,
            classification="semantic",
        )

        rendered = _publication_table_to_html(table)

        self.assertTrue(rendered.startswith('<table class="report-table"') or "table-row-list" in rendered)
        self.assertIn("Tabela 1. Priorytety kompetencyjne", rendered)
        self.assertNotIn("<p>Tabela 1. Priorytety kompetencyjne</p>", rendered)

    def test_shadow_route_decision_does_not_change_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "shadow.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Plain text publication. " * 30)
            doc.save(str(pdf_path))
            doc.close()

            with patch("publication_analysis.detect_toolchain", return_value={}):
                analysis = analyze_publication(str(pdf_path), route_model_mode="shadow")

        self.assertEqual(analysis.profile, analysis.route_decision["heuristic_profile"])
        self.assertEqual(analysis.route_decision["selected_profile"], analysis.profile)
        self.assertFalse(analysis.route_decision["override_used"])
        self.assertIn("input_features_hash", analysis.route_decision)

    def test_assist_route_decision_can_override_when_gate_allows_it(self) -> None:
        decision = {
            "heuristic_profile": "book_reflow",
            "heuristic_confidence": 0.55,
            "ml_profile": "magazine_reflow",
            "ml_confidence": 0.91,
            "selected_profile": "magazine_reflow",
            "mode": "assist",
            "override_used": True,
            "reason_codes": ["assist-override"],
            "model_version": "test",
            "input_features_hash": "abc",
            "scores": {"magazine_reflow": 0.91},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "assist.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Plain text publication. " * 30)
            doc.save(str(pdf_path))
            doc.close()

            with (
                patch("publication_analysis.detect_toolchain", return_value={}),
                patch("publication_analysis.build_route_decision", return_value=decision),
            ):
                analysis = analyze_publication(str(pdf_path), route_model_mode="assist")

        self.assertEqual(analysis.profile, "magazine_reflow")
        self.assertEqual(analysis.route_decision["selected_profile"], "magazine_reflow")
        self.assertTrue(analysis.route_decision["override_used"])

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
