from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from publication_model import PublicationAnalysis, PublicationDocument

from converter import ConversionConfig, _build_publication_pipeline_result, convert_pdf_to_epub_with_report


class PublicationBudgetSelectionTests(unittest.TestCase):
    @patch("converter.finalize_epub_bytes", return_value=(b"epub", {"status": "skipped"}))
    @patch("converter.build_epub", return_value=b"epub")
    def test_publication_pipeline_uses_original_filename_when_temp_job_id_title_is_weak(
        self,
        mock_build_epub,
        _mock_finalize_epub,
    ) -> None:
        analysis = PublicationAnalysis(
            profile="book_reflow",
            confidence=0.68,
            page_count=1990,
            render_budget_class="fixed_layout_extreme",
            has_toc=False,
            has_tables=False,
            has_diagrams=False,
            has_meaningful_images=True,
            estimated_sections=50,
            fallback_recommendation="semantic-reflow",
            ui_profile="book",
            legacy_strategy="layout_fixed",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=True,
            text_heavy=False,
            detected_features=["chess-notation-collection"],
        )
        document = PublicationDocument(
            title="3a7e54b878fe4414a54c8224242b412c",
            author="Unknown",
            language="pl",
            profile="book_reflow",
            analysis=analysis,
            metadata={},
        )

        result = _build_publication_pipeline_result(
            "C:/tmp/3a7e54b878fe4414a54c8224242b412c.pdf",
            config=ConversionConfig(language="pl"),
            analysis=analysis,
            pdf_metadata={"title": "3a7e54b878fe4414a54c8224242b412c", "author": "Unknown"},
            original_filename="876908532-Chess-Notes-the-Jobava-London-Game-Collectioin-D01-2025-1990pages.pdf",
            build_publication_document=lambda *_args, **_kwargs: document,
            publication_to_content=lambda _document: {"chapters": [], "extra_artifacts": []},
            finalize_publication_epub=lambda doc, _epub_bytes: doc.quality_report,
        )

        expected_title = "876908532-Chess-Notes-the-Jobava-London-Game-Collectioin-D01-2025-1990pages"
        self.assertEqual(result["document_summary"]["title"], expected_title)
        self.assertEqual(mock_build_epub.call_args.args[3]["title"], expected_title)

    @patch("converter._extract_pdf_metadata", return_value={"title": "Woodpecker", "author": "Authors"})
    @patch("converter._evaluate_publication_size_budget")
    @patch("converter._build_publication_pipeline_result")
    @patch("publication_analysis.analyze_publication")
    def test_publication_budget_keeps_primary_result_when_budget_only_warns(
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
        mock_build_publication_result.return_value = {
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
        }
        mock_evaluate_size_budget.return_value = {
            "status": "passed_with_warnings",
            "budget_key": "diagram_book_reflow_balanced",
            "warn_bytes": 20,
            "hard_bytes": 25,
            "inspection": {"largest_assets": []},
            "message": "primary warning",
        }

        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "woodpecker.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 publication budget probe")

            payload = convert_pdf_to_epub_with_report(
                str(pdf_path),
                config=ConversionConfig(language="en"),
                original_filename=pdf_path.name,
            )

        self.assertEqual(mock_build_publication_result.call_count, 1)
        self.assertEqual(mock_evaluate_size_budget.call_count, 1)
        self.assertEqual(payload["epub_bytes"], b"p" * 6000)
        self.assertEqual(payload["quality_report"]["size_budget_status"], "passed_with_warnings")
        self.assertEqual(payload["quality_report"]["render_budget_attempt"], "primary")

    @patch("converter._extract_pdf_metadata", return_value={"title": "Woodpecker", "author": "Authors"})
    @patch("converter._evaluate_publication_size_budget")
    @patch("converter._build_publication_pipeline_result")
    @patch("publication_analysis.analyze_publication")
    def test_publication_budget_retries_only_after_hard_failure(
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
                "status": "failed",
                "budget_key": "diagram_book_reflow_balanced",
                "warn_bytes": 20,
                "hard_bytes": 25,
                "inspection": {"largest_assets": []},
                "message": "primary hard fail",
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
        self.assertEqual(mock_evaluate_size_budget.call_count, 2)
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
