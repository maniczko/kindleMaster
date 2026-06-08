from __future__ import annotations

import io
import tempfile
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import publication_pipeline
from PIL import Image

from converter import (
    ConversionConfig,
    _apply_content_metadata_overrides,
    _apply_size_budget_metadata,
    _build_content_from_ocr,
    _build_fixed_layout_epub_once,
    _build_fixed_layout_epub_with_budget,
    _build_publication_pipeline_result,
    _clone_fixed_layout_config,
    _clone_publication_budget_config,
    _compact_int,
    _compact_semantic_cleanup_report,
    _compact_semantic_mapping,
    _parse_pdf2htmlex_output,
    _epub_css_for_content,
    _should_skip_expensive_text_cleanup,
    _detect_image_ext,
    _strip_tags,
    _publication_budget_attempt_order,
    _phase_status_payload,
    _semantic_reference_cleanup_payload,
    build_epub,
    check_pdf2htmlEX_available,
    detect_source_page_label,
    extract_plain_paragraph,
    extract_pdf_with_pymupdf,
    extract_problem_exercise_number,
    maybe_link_solution_reference,
    finalize_epub_bytes,
    maybe_link_problem_reference,
    strip_emails,
    add_problem_anchor,
    add_standalone_problem_anchor,
    extract_standalone_exercise_number,
)
from converter import (
    _publication_budget_attempt_order as publication_budget_attempt_order,
    _phase_status_payload as phase_status_payload,
    _size_gate_rank as size_gate_rank,
    _apply_size_budget_metadata as apply_size_budget_metadata,
    _clone_publication_budget_config as clone_publication_budget_config,
    _clone_fixed_layout_config as clone_fixed_layout_config,
    _render_pdf_cover_image as render_pdf_cover_image,
    detect_pdf_type,
    pdf_to_html_fixed_layout,
    _legacy_convert_pdf_to_epub,
    _detect_image_ext,
    convert_pdf_to_epub_with_report,
    optimize_image_data,
)


class ConverterCoreHelperTests(unittest.TestCase):
    def test_strip_emails_removes_mailto_and_email_and_compacts_spaces(self) -> None:
        value = "Support: mailto:test@example.com and plain@test.com  inside"
        self.assertEqual(strip_emails(value), "Support: and inside")

    def test_extract_plain_paragraph_returns_attrs_and_plain_text(self) -> None:
        self.assertEqual(extract_plain_paragraph("<p class='x'>Hello &amp; &lt;world&gt;</p>"), (" class='x'", "Hello & <world>"))
        self.assertEqual(extract_plain_paragraph("not html"), (None, None))
        self.assertEqual(extract_plain_paragraph("<div>no paragraph</div>"), (None, None))

    def test_detect_source_page_label_respects_first_numeric_candidate(self) -> None:
        self.assertEqual(
            detect_source_page_label(["<p>34</p>", "<p>7</p>", "<p>99</p>"]),
            "34",
        )
        self.assertIsNone(detect_source_page_label(["<p>Intro</p>", "<p>12</p>"]))
        self.assertEqual(detect_source_page_label(["", "<p>12</p>"]), "12")

    def test_maybe_link_solution_reference_links_when_target_exists(self) -> None:
        linked = maybe_link_solution_reference(
            "<p>Solutions page 11</p>",
            {"11": "chapter_001.xhtml"},
        )
        self.assertIn('<a href="chapter_001.xhtml#book-page-11">Solutions page 11</a>', linked)

    def test_maybe_link_solution_reference_returns_original_when_missing_target(self) -> None:
        fragment = "<p>Solutions page 11</p>"
        self.assertEqual(maybe_link_solution_reference(fragment, {"22": "chapter.xhtml"}), fragment)

    def test_extract_problem_and_standalone_exercise_number(self) -> None:
        self.assertEqual(extract_problem_exercise_number('<div class="chess-problem"><span class="exercise-number">74.</span> test</div>'), "74")
        self.assertIsNone(extract_problem_exercise_number("<p>no number</p>"))
        self.assertEqual(extract_standalone_exercise_number("<p>123</p>"), "123")
        self.assertIsNone(extract_standalone_exercise_number("<p>12345</p>"))

    def test_problem_anchor_helpers_and_links(self) -> None:
        self.assertEqual(
            add_problem_anchor('<div class="chess-problem"><span class="exercise-number">74.</span>x</div>'),
            '<div class="chess-problem" id="exercise-74"><span class="exercise-number">74.</span>x</div>',
        )
        self.assertEqual(add_problem_anchor('<div id="exercise-74" class="chess-problem">x</div>',), '<div id="exercise-74" class="chess-problem">x</div>')
        self.assertEqual(add_standalone_problem_anchor("<p>7</p>", "7"), '<p id="exercise-7">7</p>')
        self.assertEqual(
            maybe_link_solution_reference(
                "<p>74. first</p>",
                {"74": "chapter_001.xhtml#exercise-74"},
            ),
            '<p>74. first</p>'
        )
        self.assertEqual(
            maybe_link_problem_reference(
                "<p>74. first - answer</p>",
                {"74": "chapter_001.xhtml#exercise-74"},
            ),
            '<p><a class="solution-backlink" href="chapter_001.xhtml#exercise-74">74. first - answer</a></p>',
        )


class ConverterMetadataAndBudgetTests(unittest.TestCase):
    def test_clone_fixed_layout_config(self) -> None:
        cfg = ConversionConfig(prefer_fixed_layout=False)
        copied = clone_fixed_layout_config(
            cfg,
            render_budget_class="fixed_layout_dense",
            render_budget_attempt="fallback",
            warn_bytes=11,
            hard_bytes=29,
        )
        self.assertTrue(copied.prefer_fixed_layout)
        self.assertEqual(copied.render_budget_class, "fixed_layout_dense")
        self.assertEqual(copied.render_budget_attempt, "fallback")
        self.assertEqual(copied.size_budget_warn_bytes, 11)
        self.assertEqual(copied.size_budget_hard_bytes, 29)

    def test_build_fixed_layout_epub_once_prefers_v2_and_falls_back(self) -> None:
        with patch("fixed_layout_builder_v2.build_fixed_layout_epub_v2") as build_v2, patch(
            "fixed_layout_builder.build_fixed_layout_epub",
        ) as build_v1:
            build_v2.side_effect = RuntimeError("v2 unavailable")
            build_v1.return_value = b"epub-v1"
            result_bytes, builder = _build_fixed_layout_epub_once(
                "dummy.pdf",
                ConversionConfig(),
                {"title": "Test"},
            )

        self.assertEqual(result_bytes, b"epub-v1")
        self.assertEqual(builder, "fixed_layout_v1")
        self.assertTrue(build_v2.called)
        self.assertTrue(build_v1.called)

    def test_publication_budget_attempt_order_special_case(self) -> None:
        analysis = SimpleNamespace(profile="diagram_book_reflow", page_count=260)
        self.assertEqual(
            publication_budget_attempt_order(analysis, "diagram_book_reflow_balanced"),
            ("fallback", "primary"),
        )
        self.assertEqual(
            publication_budget_attempt_order(SimpleNamespace(profile="book_reflow", page_count=120), "book_reflow"),
            ("primary", "fallback"),
        )

    def test_size_gate_rank_status_priority(self) -> None:
        self.assertEqual(size_gate_rank("passed"), 3)
        self.assertEqual(size_gate_rank("passed_with_warnings"), 2)
        self.assertEqual(size_gate_rank("unavailable"), 1)
        self.assertEqual(size_gate_rank("unknown"), 0)

    def test_apply_size_budget_metadata_appends_warning_once(self) -> None:
        payload = apply_size_budget_metadata(
            {"warnings": ["alpha"]},
            size_gate={
                "status": "passed_with_warnings",
                "message": "alpha",
                "warn_bytes": 10,
                "hard_bytes": 20,
                "budget_key": "book-balanced",
                "inspection": {"entry_count": 11, "image_count": 2, "largest_assets": [{"name": "a"}]},
            },
            final_output_size_bytes=42,
            render_budget_attempt="primary",
        )
        self.assertEqual(payload["warnings"], ["alpha"])
        self.assertEqual(payload["size_budget_status"], "passed_with_warnings")
        self.assertEqual(payload["size_budget_key"], "book-balanced")

    def test_compact_semantic_cleanup_report_and_payload(self) -> None:
        report = _compact_semantic_cleanup_report(
            {
                "gates": {"F": {"status": "pass"}},
                "phases": {
                    "metadata_repair": {"status": "completed", "manual_review": [1, 2, 3]},
                    "toc_rebuild": {"status": "passed"},
                    "structural_integrity": {"status": "passed", "manual_review_count": 1},
                },
                "summary": {
                    "cleanup_scope": "semantic-reflow",
                    "chapter_count": 2,
                    "toc_entry_count_before": 1,
                    "toc_entry_count_after": 2,
                    "manual_review_count": 0,
                },
                "manual_review_queue": [{"code": "x", "message": "manual"}],
            },
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["manual_review_count"], 0)
        self.assertEqual(report["cleanup_scope"], "semantic-reflow")
        self.assertEqual(len(report["manual_review_sample"]), 1)

        self.assertEqual(
            _semantic_reference_cleanup_payload({"reference_cleanup": {"entries_rebuilt": 3, "quality": "ok"}}),
            {"entries_rebuilt": 3, "quality": "ok"},
        )
        self.assertEqual(
            _compact_semantic_mapping({"k": [i for i in range(14)]}),
            {"k": list(range(12))},
        )
        self.assertEqual(_phase_status_payload("noop"), {"status": "not_reported"})

    def test_apply_content_metadata_overrides_prefers_inferred_when_weak(self) -> None:
        metadata = _apply_content_metadata_overrides(
            {"title": "document", "author": "Unknown"},
            {
                "metadata": {
                    "inferred_publication_title": "Inferred Handbook",
                    "author": "Strong Author",
                    "publisher": "Acme",
                    "description": "Doc description",
                    "subject": "Testing",
                    "date": "2024",
                },
            },
            original_filename="document.pdf",
        )
        self.assertEqual(metadata["title"], "Inferred Handbook")
        self.assertEqual(metadata["author"], "Strong Author")
        self.assertEqual(metadata["publisher"], "Acme")
        self.assertEqual(metadata["description"], "Doc description")

    def test_should_skip_expensive_text_cleanup_requires_dense_book(self) -> None:
        self.assertTrue(
            _should_skip_expensive_text_cleanup(
                {"source_page_count": 500, "ui_profile": "technical-study"},
                publication_profile="book_reflow",
            )
        )
        self.assertFalse(_should_skip_expensive_text_cleanup({"source_page_count": 100}, publication_profile="book_reflow"))


class ConverterParserAndConversionTests(unittest.TestCase):
    def test_parse_pdf2htmlex_output_returns_none_for_empty_html(self) -> None:
        self.assertIsNone(_parse_pdf2htmlex_output({"html": "", "files": []}, ConversionConfig()))

    def test_parse_pdf2htmlex_output_collects_pages_and_images(self) -> None:
        html = """<html><body>
            <div class="page">
              <span class="t">Intro</span>
              <img src="img-1.png"/>
            </div>
          </body></html>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            img = Path(temp_dir) / "img-1.png"
            img.write_bytes(b"pngbytes")
            result = _parse_pdf2htmlex_output(
                {"html": html, "files": [{"filename": "img-1.png", "filepath": str(img)}]},
                ConversionConfig(),
            )
        self.assertEqual(result["method"], "pdf2htmlEX_parsed")
        self.assertEqual(len(result["chapters"]), 1)
        self.assertEqual(len(result["images"]), 1)
        self.assertEqual(result["images"][0]["filename"], "img-1.png")

    def test_pdf2html_available_check_and_wrapper_failure(self) -> None:
        self.assertFalse(check_pdf2htmlEX_available())

    def test_build_content_from_ocr_converts_pages_to_chapters(self) -> None:
        ocr_result = SimpleNamespace(
            engine_used="tesseract",
            pages=[
                SimpleNamespace(
                    page_num=0,
                    text="Title line\nmore\n\nThis is paragraph.\nSecond line.",
                    image_data=b"img0",
                    confidence=0.81,
                ),
                SimpleNamespace(
                    page_num=1,
                    text="Body text only",
                    image_data=b"img1",
                    confidence=0.79,
                ),
            ],
        )
        built = _build_content_from_ocr(ocr_result, ConversionConfig(), {"title": "OCR Book"})
        self.assertEqual(len(built["chapters"]), 2)
        self.assertEqual(built["chapters"][0]["html_parts"][0], "<h1>Title line\nmore</h1>")
        self.assertEqual(built["method"], "ocr_tesseract")
        self.assertTrue(built["text_content"])

    def test_extract_pdf_with_pymupdf_collects_text_and_images(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.rect = SimpleNamespace(width=612.0, height=792.0)

            def get_text(self, *_args, **_kwargs):
                return {
                    "blocks": [
                        {
                            "type": 0,
                            "bbox": [0, 0, 100, 20],
                            "lines": [
                                {
                                    "bbox": [0, 0, 100, 20],
                                    "spans": [
                                        {
                                            "text": "Big heading",
                                            "size": 20,
                                            "flags": 16,
                                            "font": "Arial",
                                            "color": 0,
                                        }
                                    ],
                                },
                            ],
                        },
                        {"type": 1, "bbox": [0, 0, 20, 20], "image": b"\x89PNG\r\n\x1a\n"},
                    ]
                }

            def get_images(self, full=True):
                return []

        class FakeDoc:
            def __init__(self) -> None:
                self.pages = [FakePage()]

            def __len__(self) -> int:
                return len(self.pages)

            def __getitem__(self, idx: int) -> FakePage:
                return self.pages[idx]

            def get_toc(self):
                return []

            def close(self) -> None:
                self.closed = True

        with patch("converter.fitz.open", return_value=FakeDoc()):
            payload = extract_pdf_with_pymupdf("book.pdf", ConversionConfig(), {"title": "Book", "source_pdf_path": "book.pdf"})

        self.assertEqual(payload["method"], "pymupdf")
        self.assertEqual(len(payload["chapters"]), 1)
        self.assertEqual(payload["chapters"][0]["title"], "Strona 1")
        self.assertEqual(payload["chapters"][0]["html_parts"][0], "<p><strong>Big heading</strong></p>")
        self.assertEqual(len(payload["images"]), 1)


class ConverterFinalizeAndHelpersTests(unittest.TestCase):
    def test_finalize_epub_bytes_text_cleanup_failure_keeps_cleanup_summary(self) -> None:
        with patch("text_normalization.clean_epub_text_package", side_effect=RuntimeError("boom")):
            with patch(
                "kindle_semantic_cleanup.finalize_epub_for_kindle",
                return_value=(b"semantic", {"entries_rebuilt": 0}),
            ):
                with patch(
                    "epub_reference_repair.repair_epub_reference_sections",
                    return_value=type(
                        "ReferenceRepairStub",
                        (),
                        {"epub_bytes": b"repaired", "summary": {"entries_rebuilt": 1}},
                    )(),
                ):
                    with patch("ai_quality_intelligence.evaluate_ai_quality_intelligence", return_value={"status": "passed"}):
                        _, summary = finalize_epub_bytes(
                            b"epub",
                            ConversionConfig(),
                            {"title": "T", "author": "A"},
                            "book.pdf",
                            return_details=True,
                        )
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["reference_cleanup"]["entries_rebuilt"], 1)

    def test_finalize_epub_bytes_semantic_cleanup_failure_still_references_reference_repair(self) -> None:
        with patch(
            "text_normalization.clean_epub_text_package",
            return_value=type(
                "CleanStub",
                (),
                {
                    "epub_bytes": b"clean",
                    "summary": {"auto_fix_count": 0, "review_needed_count": 0, "blocked_count": 0, "unknown_term_count": 0, "publish_blocked": False, "epubcheck_status": "passed"},
                    "epubcheck": {"status": "passed"},
                    "unknown_terms": [],
                    "markdown_report": "",
                    "chapter_diffs": [],
                },
            )(),
        ):
            with patch("kindle_semantic_cleanup.finalize_epub_for_kindle", side_effect=RuntimeError("crash")):
                with patch(
                    "epub_reference_repair.repair_epub_reference_sections",
                    return_value=type(
                        "ReferenceRepairStub",
                        (),
                        {"epub_bytes": b"repaired", "summary": {"entries_rebuilt": 2}},
                    )(),
                ):
                    _, summary = finalize_epub_bytes(
                        b"epub",
                        ConversionConfig(),
                        {"title": "T", "author": "A"},
                        "book.pdf",
                        return_details=True,
                    )
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["reference_cleanup"]["entries_rebuilt"], 2)

    def test_finalize_epub_bytes_ai_quality_failure_generates_stub(self) -> None:
        with patch("text_normalization.clean_epub_text_package") as clean:
            clean.return_value = type(
                "CleanStub",
                (),
                {
                    "epub_bytes": b"clean",
                    "summary": {"auto_fix_count": 0, "review_needed_count": 0, "blocked_count": 0, "unknown_term_count": 0, "publish_blocked": False, "epubcheck_status": "passed"},
                    "epubcheck": {"status": "passed"},
                    "unknown_terms": [],
                    "markdown_report": "",
                    "chapter_diffs": [],
                },
            )()
            with patch("kindle_semantic_cleanup.finalize_epub_for_kindle", return_value=(b"semantic", {"entries_rebuilt": 0})):
                with patch(
                    "epub_reference_repair.repair_epub_reference_sections",
                    return_value=type(
                        "ReferenceRepairStub",
                        (),
                        {"epub_bytes": b"repaired", "summary": {"entries_rebuilt": 0}},
                    )(),
                ):
                    with patch(
                        "openai_quality_provider.build_openai_quality_provider_from_env",
                        return_value=None,
                    ), patch(
                        "ai_quality_intelligence.evaluate_ai_quality_intelligence",
                        side_effect=RuntimeError("broken"),
                    ):
                        _, summary = finalize_epub_bytes(
                            b"epub",
                            ConversionConfig(),
                            {"title": "T", "author": "A"},
                            "book.pdf",
                            return_details=True,
                        )
        self.assertEqual(summary["ai_quality"]["status"], "unavailable")
        self.assertTrue(summary["ai_quality"]["deterministic_output_preserved"])

    def test_pdf_render_cover_image_returns_none_when_not_possible(self) -> None:
        self.assertIsNone(render_pdf_cover_image(None))
        with patch("converter.fitz.open", side_effect=RuntimeError("missing")):
            self.assertIsNone(render_pdf_cover_image("missing.pdf"))

    def test_pdf_render_cover_image_renders_first_page(self) -> None:
        fake_pix = MagicMock()
        fake_pix.tobytes.side_effect = lambda fmt=None: b"jpeg-bytes" if fmt == "jpeg" else b"fallback"

        fake_page = SimpleNamespace(
            rect=SimpleNamespace(width=100.0, height=80.0),
        )
        fake_page.get_pixmap = lambda **_kwargs: fake_pix

        fake_doc = MagicMock()
        fake_doc.__len__.return_value = 1
        fake_doc.__getitem__.return_value = fake_page
        fake_doc.close = MagicMock()

        with patch("converter.fitz.open", return_value=fake_doc):
            with patch("converter.fitz.Matrix", return_value="matrix"):
                with patch("converter.fitz.csRGB", return_value="cs"):
                    rendered = render_pdf_cover_image("book.pdf", max_dimension=250)
        self.assertEqual(rendered["filename"], "cover.jpeg")
        self.assertEqual(rendered["extension"], "jpeg")
        self.assertEqual(rendered["data"], b"jpeg-bytes")


class ConverterUtilityHelpersTests(unittest.TestCase):
    def test_image_helpers_and_css_selection(self) -> None:
        self.assertEqual(_detect_image_ext(b"\x89PNG\r\n\x1a\n"), "png")
        self.assertEqual(_detect_image_ext(b"\xFF\xD8\x00"), "jpeg")
        self.assertEqual(_strip_tags("<p>Hello <span>X</span></p>"), "Hello X")
        self.assertEqual(_strip_tags(""), "")
        self.assertEqual(_compact_int("bad"), 0)

        epub = _epub_css_for_content(
            {
                "chapters": [
                    {"html_parts": ["<div class='magazine-figure'>ok</div>"], "images": [{"is_chess": True}]},
                ],
            },
        )
        self.assertIn(".magazine-figure img", epub)
        self.assertIn(".chess-diagram", epub)

        no_magic = _epub_css_for_content({"chapters": [{"html_parts": ["<p>plain</p>"], "images": []}]})
        self.assertIn("body {", no_magic)

    def test_build_epub_no_chapters_creates_fallback_chapter(self) -> None:
        epub_bytes = build_epub({}, ConversionConfig(), "book.pdf", {"title": "Fallback", "author": "Unknown Author"})
        with io.BytesIO(epub_bytes) as payload:
            import zipfile

            with zipfile.ZipFile(payload) as archive:
                names = set(archive.namelist())
            # noqa: B018
            self.assertIn("EPUB/chapter_001.xhtml", names)
            self.assertIn("EPUB/cover.xhtml", names)

    def test_build_epub_fixed_layout_adds_layout_metadata_and_reuses_first_image_cover(self) -> None:
        img = Image.new("RGB", (16, 16), "blue")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")

        content = {
            "layout_mode": "fixed",
            "metadata": {
                "inferred_publication_title": "Inferred title",
                "author": "Inferred Author",
                "publisher": "Acme Press",
                "description": "Sample",
                "subject": "one; two; three",
            },
            "chapters": [
                {"title": "Rozdział 1", "html_parts": ["<p>Treść</p>"]},
            ],
            "images": [
                {
                    "filename": "cover.png",
                    "data": img_bytes.getvalue(),
                    "extension": "png",
                }
            ],
        }
        epub_bytes = build_epub(content, ConversionConfig(), "book.pdf", {"title": "Original title", "author": "Draft", "source_pdf_path": "book.pdf"})
        with io.BytesIO(epub_bytes) as payload:
            import zipfile

            with zipfile.ZipFile(payload) as archive:
                names = set(archive.namelist())
            self.assertIn("EPUB/style/default.css", names)
            self.assertIn("EPUB/style/fixed.css", names)
            self.assertIn("EPUB/images/cover.png", names)
            self.assertIn("EPUB/cover.xhtml", names)


class ConverterDetectAndLegacyPipelineTests(unittest.TestCase):
    def test_detect_pdf_type_identifies_scanned_and_layout_heavy(self) -> None:
        class FakePage:
            def __init__(self, text: str, image_count: int, text_area: float) -> None:
                self._text = text
                self._images = [1] * image_count
                self.rect = SimpleNamespace(width=1000, height=1000)
                block_bbox = [0, 0, text_area, text_area]
                self._dict = {
                    "blocks": [
                        {"type": 0, "bbox": block_bbox},
                    ]
                }

            def get_text(self, *args, **_kwargs) -> str | dict:
                if args and args[0] == "dict":
                    return self._dict
                return self._text

            def get_images(self, *_args, **_kwargs) -> list[tuple[int]]:
                return [(idx + 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) for idx in range(len(self._images))]

        class FakeDoc:
            def __init__(self, pages: list[FakePage]) -> None:
                self.pages = pages

            def __len__(self) -> int:
                return len(self.pages)

            def __getitem__(self, idx: int) -> FakePage:
                return self.pages[idx]

            def close(self) -> None:
                self.closed = True

        with patch("converter.fitz.open", return_value=FakeDoc([
            FakePage("A" * 60, 1, 500000),
            FakePage("A" * 60, 1, 500000),
            FakePage("A" * 60, 1, 500000),
            FakePage("A" * 60, 1, 500000),
        ])):
            layout_heavy = detect_pdf_type("book.pdf")
        self.assertTrue(layout_heavy["layout_heavy"])
        self.assertEqual(layout_heavy["recommended_strategy"], "layout_fixed")
        self.assertFalse(layout_heavy["is_scanned"])

        with patch("converter.fitz.open", return_value=FakeDoc([
            FakePage("", 1, 0),
            FakePage("", 1, 0),
            FakePage("", 1, 0),
            FakePage("", 1, 0),
        ])):
            scanned = detect_pdf_type("book.pdf")
        self.assertTrue(scanned["is_scanned"])
        self.assertEqual(scanned["recommended_strategy"], "ocr_fixed")

    def test_extract_pdf_with_pymupdf_collects_complex_blocks_and_toc_title(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.rect = SimpleNamespace(width=1000, height=1000)

            def get_text(self, *args, **_kwargs) -> dict | str:
                if args and args[0] == "dict":
                    return {
                        "blocks": [
                            {
                                "type": 0,
                                "lines": [
                                    {"spans": [{"text": "Big heading", "size": 40, "flags": 16, "font": "A", "color": 0}]},
                                    {"spans": [{"text": "Section", "size": 28, "flags": 0, "font": "A", "color": 0}]},
                                    {"spans": [{"text": "Sub", "size": 24, "flags": 0, "font": "A", "color": 0}]},
                                    {"spans": [{"text": "Body", "size": 18, "flags": 18, "font": "A", "color": 0}]},
                                    {"spans": [{"text": "  ", "size": 18, "flags": 0, "font": "A", "color": 0}]},
                                ],
                                "bbox": [0, 0, 100, 100],
                            },
                            {"type": 1, "image": b"\x89PNG\r\n\x1a\n", "bbox": [1, 1, 2, 2]},
                        ],
                    }
                return "text layer"

            def get_images(self, full=True) -> list[tuple[int]]:
                return [(1,), (2,), (3,)]

            def extract_image(self, _xref: int) -> dict:
                if _xref in (1, 2, 3):
                    return {"image": b"\x89PNG\r\n\x1a\n", "ext": "png"}
                raise RuntimeError("extract failed")

        class FakeDoc:
            def __init__(self) -> None:
                self.pages = [FakePage()]

            def __len__(self) -> int:
                return len(self.pages)

            def __getitem__(self, idx: int) -> FakePage:
                return self.pages[idx]

            def extract_image(self, _xref: int) -> dict:
                if _xref in (1, 2, 3):
                    return {"image": b"\x89PNG\r\n\x1a\n", "ext": "png"}
                return {}

            def get_toc(self) -> list[tuple[int, str, int]]:
                return [(1, "Tabela treści", 1)]

            def close(self) -> None:
                self.closed = True

        with patch("converter._extract_pdf_metadata", return_value={"title": "fallback", "author": "auth"}), patch(
            "converter._detect_image_ext",
            return_value="png",
        ):
            with patch("converter.fitz.open", return_value=FakeDoc()):
                payload = extract_pdf_with_pymupdf("book.pdf", ConversionConfig(), None)

        self.assertEqual(payload["method"], "pymupdf")
        self.assertEqual(payload["chapters"][0]["title"], "Tabela treści")
        self.assertGreaterEqual(len(payload["chapters"][0]["html_parts"]), 5)
        self.assertEqual(len(payload["images"]), 4)

    def test_legacy_convert_pdf_to_epub_takes_magazine_reflow_path(self) -> None:
        with patch("converter._extract_pdf_metadata", return_value={"title": "Doc", "author": "Author", "source_pdf_path": "book.pdf"}):
            with patch(
                "converter.detect_pdf_type",
                return_value={
                    "has_text_layer": True,
                    "is_scanned": False,
                    "layout_heavy": True,
                    "recommended_strategy": "layout_fixed",
                    "has_images": True,
                    "text_heavy": False,
                    "page_count": 12,
                    "scanned_pages": 0,
                    "text_pages": 10,
                    "image_pages": 12,
                    "image_page_ratio": 1,
                    "text_page_ratio": 0.8,
                    "scanned_page_ratio": 0.0,
                },
            ):
                with patch(
                    "magazine_kindle_reflow.convert_magazine_to_kindle_reflow",
                    return_value={
                        "method": "magazine-kindle-reflow",
                        "text_content": True,
                        "success": True,
                        "chapters": [],
                        "images": [],
                    },
                ) as convert_mag:
                    with patch("converter.build_epub", return_value=b"reflowed") as build:
                        with patch("converter._apply_content_metadata_overrides", side_effect=lambda *args, **_kwargs: args[0]):
                            with patch("converter.finalize_epub_bytes") as finalize:
                                output = _legacy_convert_pdf_to_epub("book.pdf", ConversionConfig(prefer_fixed_layout=False), "book.pdf")
        self.assertEqual(output, b"reflowed")
        convert_mag.assert_called_once()
        build.assert_called_once()
        finalize.assert_not_called()

    def test_legacy_convert_pdf_to_epub_uses_magazine_hybrid_path_and_finalize(self) -> None:
        class FakeTmp:
            def __init__(self) -> None:
                self.name = os.path.join(tempfile.gettempdir(), "converter-hybrid.epub")

            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

        with patch("converter.detect_pdf_type", return_value={
            "has_text_layer": True,
            "is_scanned": False,
            "layout_heavy": False,
            "recommended_strategy": "layout_fixed",
            "text_heavy": False,
            "has_images": True,
            "page_count": 7,
            "scanned_pages": 0,
            "text_pages": 7,
            "image_pages": 7,
            "image_page_ratio": 1.0,
            "text_page_ratio": 1.0,
            "scanned_page_ratio": 0.0,
        }):
            with patch("converter.tempfile.NamedTemporaryFile", return_value=FakeTmp()):
                with patch("magazine_hybrid_converter_v3.convert_magazine_optimized") as convert_mag:
                    def _write_out(_pdf_path: str, out_path: str, **_kwargs: object) -> None:
                        with open(out_path, "wb") as stream:
                            stream.write(b"hybrid-epub")

                    convert_mag.side_effect = _write_out
                    with patch("converter.finalize_epub_bytes", return_value=b"final-hybrid") as finalize:
                        output = _legacy_convert_pdf_to_epub("book.pdf", ConversionConfig(prefer_fixed_layout=False), "book.pdf")
                        convert_mag.assert_called_once()
                        finalize.assert_called_once()
                        self.assertEqual(output, b"final-hybrid")


class ConverterImageAndPipelineReportsTests(unittest.TestCase):
    def test_optimize_image_data_keeps_original_when_compression_disabled(self) -> None:
        original = b"\x89PNG\r\n\x1a\n"
        cfg = ConversionConfig(compress_images=False)
        self.assertEqual(optimize_image_data(original, cfg), original)

    def test_optimize_image_data_quantizes_graphic_and_handles_colors(self) -> None:
        img = Image.new("RGBA", (16, 16))
        for x in range(16):
            for y in range(16):
                if (x + y) % 2 == 0:
                    img.putpixel((x, y), (255, 255, 255, 255))
                else:
                    img.putpixel((x, y), (0, 0, 0, 255))
        payload = io.BytesIO()
        img.save(payload, format="PNG")
        output = optimize_image_data(payload.getvalue(), ConversionConfig())
        self.assertTrue(output.startswith(b"\x89PNG"))
        self.assertNotEqual(output, payload.getvalue())

    def test_optimize_image_data_returns_original_when_open_fails(self) -> None:
        with patch("converter.Image.open", side_effect=RuntimeError("boom")):
            original = b"\x00\x01\x02"
            result = optimize_image_data(original, ConversionConfig())
        self.assertEqual(result, original)

    def test_optimize_image_data_converts_rgb_when_many_colors(self) -> None:
        img = Image.new("RGB", (4, 4), "red")
        payload = io.BytesIO()
        img.save(payload, format="PNG")
        output = optimize_image_data(payload.getvalue(), ConversionConfig(image_quality=75))
        self.assertTrue(output.startswith(b"\xff\xd8") or output.startswith(b"\x89PNG"))

        # Large color diversity should use JPEG-style optimization path.
        diversified = Image.new("RGB", (20, 20))
        for x in range(20):
            for y in range(20):
                diversified.putpixel((x, y), ((x * 13) % 256, (y * 11) % 256, ((x + y) * 7) % 256))
        payload2 = io.BytesIO()
        diversified.save(payload2, format="PNG")
        output2 = optimize_image_data(payload2.getvalue(), ConversionConfig(image_quality=77))
        self.assertTrue(output2.startswith(b"\xff\xd8") or output2.startswith(b"\x89PNG"))

    def test_convert_pdf_to_epub_with_report_uses_publication_pipeline_without_budget(self) -> None:
        publication_result = {
            "epub_bytes": b"pipeline-epub",
            "quality_report": {"validation_status": "passed"},
            "document": {"title": "Doc", "author": "Auth"},
            "document_summary": {
                "title": "Doc",
                "author": "Auth",
                "language": "en",
                "profile": "book_reflow",
                "layout_mode": "reflowable",
                "section_count": 1,
                "asset_count": 1,
            },
        }
        with patch("converter._extract_pdf_metadata", return_value={"title": "Doc", "author": "Auth"}):
            with patch(
                "publication_analysis.analyze_publication",
                return_value=SimpleNamespace(profile="book_reflow", page_count=10, ui_profile="book"),
            ):
                with patch("size_budget_policy.resolve_publication_size_budget", return_value=(None, None)):
                    with patch(
                        "converter._build_publication_pipeline_result",
                        return_value=publication_result,
                    ):
                        payload = convert_pdf_to_epub_with_report(
                            "book.pdf",
                            ConversionConfig(profile="book", language="en"),
                            "book.pdf",
                        )
        self.assertEqual(payload["epub_bytes"], b"pipeline-epub")
        self.assertEqual(payload["quality_report"]["validation_status"], "passed")
        self.assertEqual(payload["quality_report"]["final_output_size_bytes"], 13)
        self.assertIn("document_summary", payload)


class ConverterPdf2HtmlAndDetectionCoverageTests(unittest.TestCase):
    def test_pdf2html_conversion_builds_html_and_file_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_html = Path(temp_dir) / "output.html"
            output_html.write_text("<html><body>OK</body></html>", encoding="utf-8")
            (Path(temp_dir) / "asset.txt").write_text("asset", encoding="utf-8")
            run = patch("converter.subprocess.run", return_value=SimpleNamespace(returncode=0, stderr=""))
            with patch("converter.check_pdf2htmlEX_available", return_value=True):
                with run:
                    result = pdf_to_html_fixed_layout("book.pdf", temp_dir, ConversionConfig())
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "pdf2htmlEX")
        self.assertEqual(result["html"], "<html><body>OK</body></html>")
        self.assertEqual({entry["filename"] for entry in result["files"]}, {"output.html", "asset.txt"})

    def test_pdf2html_conversion_fails_when_process_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("converter.check_pdf2htmlEX_available", return_value=True):
                with patch(
                    "converter.subprocess.run",
                    return_value=SimpleNamespace(returncode=2, stderr="broken"),
                ):
                    with self.assertRaises(RuntimeError):
                        pdf_to_html_fixed_layout("book.pdf", temp_dir, ConversionConfig())

    def test_pdf2html_conversion_fails_when_output_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("converter.check_pdf2htmlEX_available", return_value=True):
                with patch("converter.subprocess.run", return_value=SimpleNamespace(returncode=0, stderr="")):
                    with patch("converter.os.path.exists", return_value=False):
                        with self.assertRaises(RuntimeError):
                            pdf_to_html_fixed_layout("book.pdf", temp_dir, ConversionConfig())

    def test_pdf2html_conversion_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("converter.check_pdf2htmlEX_available", return_value=True):
                with patch("converter.subprocess.run", side_effect=subprocess.TimeoutExpired(["pdf2htmlEX"], 300)):
                    with self.assertRaises(RuntimeError):
                        pdf_to_html_fixed_layout("book.pdf", temp_dir, ConversionConfig())

    def test_detect_pdf_type_zero_pages_is_text_reflowable(self) -> None:
        class FakeDoc:
            def __len__(self) -> int:
                return 0

            def close(self) -> None:
                return None

        with patch("converter.fitz.open", return_value=FakeDoc()):
            result = detect_pdf_type("book.pdf")
        self.assertEqual(result["page_count"], 0)
        self.assertEqual(result["recommended_strategy"], "text_reflowable")
        self.assertFalse(result["is_scanned"])
        self.assertFalse(result["has_images"])

    def test_detect_pdf_type_classifies_scanned_image_only_page(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.rect = SimpleNamespace(width=100.0, height=100.0)

            def get_text(self, *_args: object, **_kwargs: object) -> str:
                return ""

            def get_images(self, *_args: object, **_kwargs: object) -> list[tuple[int]]:
                return [(1,)]

            def get_text(self, *_args: object, **_kwargs: object) -> str:
                return ""

        class FakeDoc:
            def __init__(self) -> None:
                self.pages = [FakePage()]

            def __len__(self) -> int:
                return len(self.pages)

            def __getitem__(self, index: int) -> FakePage:
                return self.pages[index]

            def close(self) -> None:
                return None

        with patch("converter.fitz.open", return_value=FakeDoc()):
            result = detect_pdf_type("book.pdf")
        self.assertEqual(result["recommended_strategy"], "ocr_fixed")
        self.assertTrue(result["is_scanned"])


class ConverterLegacyPipelineEdgeTests(unittest.TestCase):
    def test_render_pdf_cover_image_returns_none_when_pdf_is_empty(self) -> None:
        fake_doc = MagicMock()
        fake_doc.__len__.return_value = 0
        fake_doc.close = MagicMock()
        with patch("converter.fitz.open", return_value=fake_doc):
            self.assertIsNone(render_pdf_cover_image("book.pdf"))

    def test_render_pdf_cover_image_falls_back_to_png(self) -> None:
        fake_doc = MagicMock()
        fake_pix = MagicMock()

        def tobytes(fmt: str | None = None) -> bytes:
            if fmt == "jpeg":
                raise RuntimeError("no jpeg codec")
            return b"png-bytes"

        fake_pix.tobytes = MagicMock(side_effect=tobytes)
        fake_page = MagicMock(
            rect=SimpleNamespace(width=100.0, height=100.0),
            get_pixmap=MagicMock(return_value=fake_pix),
        )
        fake_doc.__len__.return_value = 1
        fake_doc.__getitem__.return_value = fake_page
        fake_doc.close = MagicMock()

        with patch("converter.fitz.open", return_value=fake_doc):
            with patch("converter.fitz.Matrix", return_value="matrix"):
                with patch("converter.fitz.csRGB", return_value="rgb"):
                    result = render_pdf_cover_image("book.pdf", max_dimension=200)

        self.assertEqual(result["extension"], "png")
        self.assertEqual(result["data"], b"png-bytes")

    def test_parse_pdf2htmlex_output_handles_missing_image_references(self) -> None:
        html = """<html><body>
            <div class="page">
              <span class="t">Hello <mailto>mail@ex.com</mailto></span>
              <img src="missing.png"/>
            </div>
          </body></html>"""
        result = _parse_pdf2htmlex_output(
            {"html": html, "files": [{"filename": "not-a-match.png", "filepath": str(Path("/tmp/nope"))}]},
            ConversionConfig(),
        )
        self.assertEqual(result["method"], "pdf2htmlEX_parsed")
        self.assertEqual(len(result["chapters"]), 1)
        self.assertEqual(len(result["chapters"][0]["html_parts"]), 1)
        self.assertEqual(result["images"], [])

    def test_build_fixed_layout_epub_with_budget_retries_and_fails(self) -> None:
        first_gate = {
            "status": "failed",
            "message": "primary too large",
            "warn_bytes": 10,
            "hard_bytes": 20,
            "budget_key": "fixed_layout_safe",
            "inspection": {"entry_count": 10, "image_count": 2, "largest_assets": []},
        }
        second_gate = {
            **first_gate,
            "status": "failed",
            "message": "fallback too large",
        }
        with patch("converter.normalize_budget_key", return_value="fixed_layout_safe"), patch(
            "converter.get_render_budget_policy",
            return_value={"warn_bytes": 10, "hard_bytes": 20},
        ), patch(
            "converter._clone_fixed_layout_config",
            side_effect=lambda cfg, **_kwargs: cfg,
        ), patch(
            "converter._build_fixed_layout_epub_once",
            side_effect=[(b"primary", "v2"), (b"fallback", "v2")],
        ), patch(
            "converter.finalize_epub_bytes",
            return_value=b"finalized",
        ), patch(
            "converter.inspect_epub_archive",
            return_value={"entry_count": 0, "image_count": 0},
        ), patch(
            "converter.evaluate_size_budget",
            side_effect=[first_gate, second_gate],
        ):
            with self.assertRaises(Exception):
                _build_fixed_layout_epub_with_budget(
                    "book.pdf",
                    config=ConversionConfig(),
                    pdf_metadata={"title": "Doc"},
                    original_filename="book.pdf",
                    render_budget_class="fixed_layout_safe",
                )

    def test_build_fixed_layout_epub_with_budget_returns_payload_on_pass(self) -> None:
        gate = {
            "status": "passed",
            "message": "ok",
            "warn_bytes": 10,
            "hard_bytes": 20,
            "budget_key": "fixed_layout_safe",
            "inspection": {"entry_count": 0, "image_count": 0},
        }
        with patch("converter.normalize_budget_key", return_value="fixed_layout_safe"), patch(
            "converter.get_render_budget_policy",
            return_value={"warn_bytes": 10, "hard_bytes": 20},
        ), patch(
            "converter._clone_fixed_layout_config",
            side_effect=lambda cfg, **_kwargs: cfg,
        ), patch(
            "converter._build_fixed_layout_epub_once",
            return_value=(b"primary", "v2"),
        ), patch(
            "converter.finalize_epub_bytes",
            return_value=b"finalized",
        ), patch(
            "converter.inspect_epub_archive",
            return_value={"entry_count": 2, "image_count": 1},
        ), patch("converter.evaluate_size_budget", return_value=gate):
            finalized_bytes, details = _build_fixed_layout_epub_with_budget(
                "book.pdf",
                config=ConversionConfig(),
                pdf_metadata={"title": "Doc"},
                original_filename="book.pdf",
                render_budget_class="fixed_layout_safe",
            )
        self.assertEqual(finalized_bytes, b"finalized")
        self.assertEqual(details["size_budget_status"], "passed")
        self.assertEqual(details["render_budget_attempt"], "primary")

    def test_build_publication_pipeline_result_populates_payload(self) -> None:
        analysis = SimpleNamespace(profile="book_reflow", page_count=123, ui_profile="book")
        document = SimpleNamespace(
            title="Test Doc",
            author="Writer",
            language="en",
            profile="book_reflow",
            sections=[1, 2],
            assets=[1],
            metadata={"source_metadata": {"publisher": "Acme", "description": "Desc", "subject": "Math", "date": "2024"}},
            quality_report=SimpleNamespace(text_cleanup={}),
            to_dict=lambda: {"title": "Test Doc", "author": "Writer"},
        )

        def _build_doc(*_args: object, **_kwargs: object) -> object:
            return document

        with patch("publication_pipeline.publication_to_content", return_value={"chapters": []}), patch(
            "converter.build_epub",
            return_value=b"epub",
        ):
            with patch(
                "converter.finalize_epub_bytes",
                return_value=(b"final", {"status": "passed", "epubcheck_status": "passed"}),
            ):
                with patch("publication_pipeline.finalize_publication_epub", return_value=SimpleNamespace(to_dict=lambda: {"status": "ok"})):
                    payload = _build_publication_pipeline_result(
                        "book.pdf",
                        config=ConversionConfig(),
                        analysis=analysis,
                        pdf_metadata={"title": "Meta", "author": "MetaAuthor", "subject": "S"},
                        original_filename="book.pdf",
                        build_publication_document=_build_doc,
                        publication_to_content=publication_pipeline.publication_to_content,
                        finalize_publication_epub=publication_pipeline.finalize_publication_epub,
                    )
        self.assertEqual(payload["quality_report"], {"status": "ok"})
        self.assertEqual(payload["document_summary"]["layout_mode"], "reflowable")
        self.assertEqual(payload["document_summary"]["section_count"], 2)

    def test_legacy_convert_pdf_to_epub_falls_back_from_chess_to_pymupdf(self) -> None:
        detect = {
            "is_scanned": False,
            "has_text_layer": False,
            "has_images": False,
            "text_heavy": False,
            "layout_heavy": False,
            "recommended_strategy": "text_reflowable",
            "has_images": False,
            "image_pages": 0,
            "text_pages": 0,
            "page_count": 2,
            "scanned_pages": 0,
            "image_page_ratio": 0.0,
            "text_page_ratio": 0.0,
            "scanned_page_ratio": 0.0,
        }
        with patch("converter.detect_pdf_type", return_value=detect), patch(
            "converter._extract_pdf_metadata",
            return_value={"title": "Doc", "author": "Author"},
        ), patch(
            "converter._apply_content_metadata_overrides",
            side_effect=lambda pdf_metadata, content, original_filename="document.pdf": pdf_metadata,
        ), patch(
            "pymupdf_chess_extractor.extract_pdf_with_chess_support",
            side_effect=RuntimeError("no chess support"),
        ), patch(
            "converter.extract_pdf_with_pymupdf",
            return_value={"success": True, "chapters": [], "images": [], "text_content": True, "method": "pymupdf"},
        ), patch("converter.build_epub", return_value=b"raw"), patch(
            "converter.finalize_epub_bytes",
            return_value=b"final",
        ):
            payload = _legacy_convert_pdf_to_epub("book.pdf", ConversionConfig(prefer_fixed_layout=False), "book.pdf")
        self.assertEqual(payload, b"final")

    def test_convert_pdf_to_epub_with_report_preserve_layout_falls_back_to_fixed_builder(self) -> None:
        with patch("converter._extract_pdf_metadata", return_value={"title": "Doc", "author": "Auth"}):
            with patch(
                "publication_analysis.analyze_publication",
                return_value=SimpleNamespace(profile="fixed_layout_fallback", render_budget_class="fixed_layout_safe"),
            ):
                with patch("converter._build_fixed_layout_epub_with_budget", return_value=(
                    b"fixed-epub",
                    {"render_budget_class": "fixed_layout_safe", "render_budget_attempt": "primary", "size_budget_status": "passed", "size_budget_message": "", "target_warn_bytes": 1, "target_hard_bytes": 2, "final_output_size_bytes": 10},
                )):
                    payload = convert_pdf_to_epub_with_report("book.pdf", ConversionConfig(profile="preserve-layout"), "book.pdf")
        self.assertEqual(payload["source_type"], "pdf")
        self.assertEqual(payload["epub_bytes"], b"fixed-epub")
        self.assertEqual(payload["document_summary"]["layout_mode"], "fixed-layout")
        self.assertEqual(payload["quality_report"]["validation_messages"], ["Build wykonany sciezka fallback preserve-layout."])

    def test_convert_pdf_to_epub_with_report_legacy_fallback_on_exception(self) -> None:
        with patch("converter._extract_pdf_metadata", return_value={"title": "Doc", "author": "Auth"}):
            with patch("publication_analysis.analyze_publication", side_effect=RuntimeError("boom")):
                with patch("converter._legacy_convert_pdf_to_epub", return_value=b"legacy"):
                    payload = convert_pdf_to_epub_with_report("book.pdf", ConversionConfig(), "book.pdf")
        self.assertEqual(payload["analysis"]["profile"], "legacy-fallback")
        self.assertEqual(payload["epub_bytes"], b"legacy")
