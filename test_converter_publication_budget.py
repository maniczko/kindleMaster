from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from publication_model import PublicationAnalysis

from converter import ConversionConfig, convert_pdf_to_epub_with_report


class PublicationBudgetSelectionTests(unittest.TestCase):
    @patch("converter._extract_pdf_metadata", return_value={"title": "Woodpecker", "author": "Authors"})
    @patch("converter._evaluate_publication_size_budget")
    @patch("converter._build_publication_pipeline_result")
    @patch("publication_analysis.analyze_publication")
    def test_publication_budget_tries_fallback_after_primary_warning_and_keeps_smaller_result(
        self,
        mock_analyze_publication,
        mock_build_publication_result,
        mock_evaluate_size_budget,
        _mock_extract_pdf_metadata,
    ) -> None:
        mock_analyze_publication.return_value = PublicationAnalysis(
            profile="diagram_book_reflow",
            confidence=0.88,
            page_count=120,
            render_budget_class="fixed_layout_extreme",
            has_toc=True,
            has_tables=False,
            has_diagrams=True,
            has_meaningful_images=True,
            estimated_sections=7,
            fallback_recommendation="semantic-reflow",
            ui_profile="book",
            legacy_strategy="image-first-reflow",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=False,
        )
        mock_build_publication_result.side_effect = [
            {
                "epub_bytes": b"p" * 6000,
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document": None,
                "document_summary": {
                    "title": "Woodpecker",
                    "author": "Authors",
                    "profile": "diagram_book_reflow",
                    "layout_mode": "reflowable",
                    "section_count": 20,
                    "asset_count": 1164,
                },
            },
            {
                "epub_bytes": b"f" * 4200,
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document": None,
                "document_summary": {
                    "title": "Woodpecker",
                    "author": "Authors",
                    "profile": "diagram_book_reflow",
                    "layout_mode": "reflowable",
                    "section_count": 20,
                    "asset_count": 1164,
                },
            },
        ]
        mock_evaluate_size_budget.side_effect = [
            {
                "status": "passed_with_warnings",
                "budget_key": "diagram_book_reflow_balanced",
                "warn_bytes": 20,
                "hard_bytes": 25,
                "inspection": {"largest_assets": []},
                "message": "primary warning",
            },
            {
                "status": "passed",
                "budget_key": "diagram_book_reflow_balanced",
                "warn_bytes": 20,
                "hard_bytes": 25,
                "inspection": {"largest_assets": []},
                "message": "fallback pass",
            },
        ]

        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "woodpecker.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 publication budget probe")

            payload = convert_pdf_to_epub_with_report(
                str(pdf_path),
                config=ConversionConfig(language="en"),
                original_filename=pdf_path.name,
            )

        self.assertEqual(mock_build_publication_result.call_count, 2)
        self.assertEqual(payload["epub_bytes"], b"f" * 4200)
        self.assertEqual(payload["quality_report"]["size_budget_status"], "passed")
        self.assertEqual(payload["quality_report"]["render_budget_attempt"], "fallback")

    @patch("converter._extract_pdf_metadata", return_value={"title": "Woodpecker", "author": "Authors"})
    @patch("converter._evaluate_publication_size_budget")
    @patch("converter._build_publication_pipeline_result")
    @patch("publication_analysis.analyze_publication")
    def test_large_diagram_books_try_fallback_first_for_balanced_budget(
        self,
        mock_analyze_publication,
        mock_build_publication_result,
        mock_evaluate_size_budget,
        _mock_extract_pdf_metadata,
    ) -> None:
        mock_analyze_publication.return_value = PublicationAnalysis(
            profile="diagram_book_reflow",
            confidence=0.88,
            page_count=394,
            render_budget_class="fixed_layout_extreme",
            has_toc=True,
            has_tables=False,
            has_diagrams=True,
            has_meaningful_images=True,
            estimated_sections=7,
            fallback_recommendation="semantic-reflow",
            ui_profile="book",
            legacy_strategy="image-first-reflow",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=False,
        )

        captured_attempts: list[str] = []

        def fake_build(_pdf_path, *, config, **_kwargs):
            captured_attempts.append(config.diagram_budget_attempt)
            return {
                "epub_bytes": b"f" * 4200,
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document": None,
                "document_summary": {
                    "title": "Woodpecker",
                    "author": "Authors",
                    "profile": "diagram_book_reflow",
                    "layout_mode": "reflowable",
                    "section_count": 20,
                    "asset_count": 1164,
                },
            }

        mock_build_publication_result.side_effect = fake_build
        mock_evaluate_size_budget.return_value = {
            "status": "passed",
            "budget_key": "diagram_book_reflow_balanced",
            "warn_bytes": 20,
            "hard_bytes": 25,
            "inspection": {"largest_assets": []},
            "message": "fallback pass",
        }

        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "woodpecker.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 publication budget probe")

            payload = convert_pdf_to_epub_with_report(
                str(pdf_path),
                config=ConversionConfig(language="en"),
                original_filename=pdf_path.name,
            )

        self.assertEqual(captured_attempts, ["fallback"])
        self.assertEqual(payload["quality_report"]["render_budget_attempt"], "fallback")


if __name__ == "__main__":
    unittest.main()
