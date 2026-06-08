import unittest
import io
from collections import Counter
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup

from kindle_semantic_cleanup import (
    _apply_magazine_assignments,
    _classify_magazine_feature_buckets,
    _build_inventory_conflicts,
    _classify_reading_flow_document,
    _build_metadata_phase_report,
    _classify_frontmatter_signature_blocks,
    _collect_reference_link_candidates,
    _collect_repeated_short_texts,
    _collect_fallback_game_targets,
    _extract_reference_entries_from_block,
    _collect_structural_integrity_summary,
    _cleanup_solution_chapter,
    _collect_heading_candidates_from_text,
    _detect_cleanup_scope,
    _rebuild_reference_sections,
    _empty_reference_report,
    _finalize_reference_report,
    _evaluate_heading_gate,
    _evaluate_inventory_gate,
    _evaluate_metadata_gate,
    _evaluate_release_gate,
    _evaluate_structural_gate,
    _evaluate_toc_gate,
    _gate_result,
    _classify_heading_decision,
    _heading_candidate_looks_like_layout_artifact,
    _is_pseudo_heading_candidate,
    _looks_like_publication_title_candidate,
    _demote_repetitive_schema_headings,
    _extract_front_matter_title_candidate,
    _extract_author_from_chapters,
    _initialize_finalize_phase_report,
    _find_matching_heading_candidate,
    _format_solution_variation_html,
    _is_clean_reading_profile,
    _is_learning_mode_profile,
    _build_toc_map,
    _normalize_existing_table_html,
    _normalize_key,
    _normalize_figure_html,
    _normalize_transformed_table_html,
    _plan_clean_reading_flow,
    _process_chapter,
    _publication_profile_key,
    _resolve_relative_href,
    _safe_relative_path,
    _promote_heading_blocks,
    _strip_inline_ai_note_blocks,
    _resolve_heading_target,
    _should_include_in_toc,
    _should_suppress_heading_review_item,
    _dedupe_manual_review_items,
    _solution_title_match_score,
    _looks_like_magazine_boundary_text,
    _find_fallback_magazine_boundary,
    _merge_reference_report,
    _split_definition_list_items,
    _split_inline_ordered_list_items,
    _toc_entries_follow_spine_order,
    _rewrite_solution_backlinks,
    _repair_exercise_chapter,
    _repair_name_index_chapter,
    _repair_training_book_package,
    _extract_logical_blocks,
    _expand_semantic_blocks,
    _build_magazine_chapter_info,
    _derive_package_metadata,
    _trim_trailing_nonessential_figures,
    _inject_problem_solution_links,
    _split_inline_semicolon_list_items,
    _wants_rich_finalize_report,
    _audit_diagram_presentation,
    _repair_symbol_key_chapter,
    _repair_magazine_package,
    finalize_epub_for_kindle,
)


class KindleSemanticCleanupCoverageBoostTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, bytes | str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, payload in files.items():
                content = payload.encode("utf-8") if isinstance(payload, str) else payload
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, content, compress_type=compress_type)
        return output.getvalue()

    def _write_chapter(self, root: Path, name: str, content: str) -> Path:
        path = root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_split_definition_list_items_returns_list_items(self):
        block = {
            "type": "paragraph",
            "text": "Alpha: this is a clear description of the first concept; Beta: this is a longer description of the second concept",
            "class_name": "",
        }
        items = _split_definition_list_items(block)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["type"], "list-item")
        self.assertIn("Alpha", items[0]["text"])

    def test_split_inline_ordered_list_items_with_sequential_markers(self):
        block = {
            "type": "paragraph",
            "text": "1. First item; 2. Second item; 3. Third item",
            "class_name": "",
        }
        items = _split_inline_ordered_list_items(block)

        self.assertEqual(len(items), 3)
        self.assertTrue(all(item["list_kind"] == "ol" for item in items))
        self.assertTrue(all("ordered-item" in item["class_name"] for item in items))

    def test_split_inline_semicolon_list_items_with_ordered_marks(self):
        block = {
            "type": "paragraph",
            "text": "1) One; 2) Two; 3) Three",
            "class_name": "",
        }
        items = _split_inline_semicolon_list_items(block)

        self.assertEqual(len(items), 3)
        self.assertTrue(all(item["list_kind"] == "ol" for item in items))
        self.assertTrue(all("ordered-item" in item["class_name"] for item in items))

    def test_promote_heading_blocks_marks_headings_and_index_letters(self):
        body_blocks = [{"type": "paragraph", "text": "Introduction", "class_name": "", "is_top": True}]
        promoted_body = _promote_heading_blocks(body_blocks, section_context="body")
        self.assertEqual(promoted_body[0]["type"], "heading")
        self.assertEqual(promoted_body[0]["level"], 2)

        index_blocks = [{"type": "paragraph", "text": "A", "class_name": "", "is_top": True}]
        promoted_index = _promote_heading_blocks(index_blocks, section_context="index")
        self.assertEqual(promoted_index[0]["type"], "heading")
        self.assertEqual(promoted_index[0]["level"], 3)

    def test_promote_heading_blocks_preserves_dense_short_frontmatter_paragraph(self):
        dense_frontmatter = [
            {"type": "paragraph", "text": "Short", "class_name": "", "is_top": True},
            {"type": "paragraph", "text": "More short text", "class_name": "", "is_top": True},
            {"type": "paragraph", "text": "Another short line", "class_name": "", "is_top": True},
            {"type": "paragraph", "text": "Additional short phrase", "class_name": "", "is_top": True},
            {"type": "paragraph", "text": "Yet another short note", "class_name": "", "is_top": True},
            {"type": "paragraph", "text": "More note text", "class_name": "", "is_top": False},
        ]
        promoted = _promote_heading_blocks(dense_frontmatter, section_context="body")

        self.assertEqual(promoted[0]["type"], "paragraph")
        self.assertEqual(promoted[0]["text"], "Short")

    def test_demote_repetitive_schema_headings_keeps_first_two(self):
        blocks = [
            {"type": "heading", "text": "Co to jest", "level": 2},
            {"type": "heading", "text": "Co to jest", "level": 2},
            {"type": "heading", "text": "Co to jest", "level": 2},
        ]
        adjusted = _demote_repetitive_schema_headings(blocks)

        self.assertEqual(adjusted[0]["type"], "heading")
        self.assertEqual(adjusted[1]["type"], "heading")
        self.assertEqual(adjusted[2]["type"], "paragraph")
        self.assertIn("demoted-schema-heading", adjusted[2]["class_name"])

    def test_format_solution_variation_html_adds_breaks_on_or_marker(self):
        compact = _format_solution_variation_html("1. e4 e5 Or 2. d4 d5 Or 3. Nf3 Nf6")
        self.assertIn("<br/>", compact)

        no_break = _format_solution_variation_html("1. e4 e5 2. d4 d5 3. Nf3 Nf6")
        self.assertNotIn("<br/>", no_break)

    def test_normalize_figure_html_keeps_diagram_caption_and_photo_alt(self):
        regular = BeautifulSoup('<figure><img src="photo.png"/><figcaption>Photo caption</figcaption></figure>', "xml").figure
        normalized_regular = _normalize_figure_html(regular)
        soup_regular = BeautifulSoup(normalized_regular, "xml")
        figure_regular = soup_regular.find("figure")
        self.assertEqual(figure_regular.name, "figure")
        self.assertIn("figure", normalized_regular)
        self.assertIn("figure-caption", normalized_regular)
        self.assertEqual(figure_regular.img.get("alt"), "Photo caption")
        self.assertIn("technical-figure", figure_regular.get("class", []))

        chess = BeautifulSoup(
            '<figure id="chess-1"><img class="chess-diagram" src="diagram.png"/><figcaption class="diagram-caption">Sample game</figcaption></figure>',
            "xml",
        ).figure
        normalized_chess = _normalize_figure_html(chess)
        chess_soup = BeautifulSoup(normalized_chess, "xml")
        chess_figure = chess_soup.find("figure")
        self.assertIn("chess-problem", chess_figure.get("class", []))
        self.assertEqual(chess_figure.get("id"), "chess-1")
        self.assertEqual(chess_figure.img.get("alt"), "Sample game")

    def test_normalize_existing_table_html_sanitizes_attributes_and_removes_direct_text(self):
        table = BeautifulSoup(
            '<table class="report-table" data-bad="bad"><caption>Summary table</caption><tbody><tr><td style="color:red">Cell</td></tr></tbody></table>',
            "xml",
        ).table
        normalized = _normalize_existing_table_html(table)
        soup = BeautifulSoup(normalized, "xml")

        self.assertIsNotNone(soup.table)
        self.assertIn("semantic-table", soup.table.get("class", []))
        self.assertIn("Summary table", normalized)
        self.assertIn("Cell", normalized)

    def test_normalize_transformed_table_html_strips_disallowed_nodes(self):
        node = BeautifulSoup(
            '<section class="row-list report" data-source="x"><div>bad</div><p>Line one</p><a href="javascript:alert(1)">x</a></section>',
            "xml",
        ).section
        normalized = _normalize_transformed_table_html(node)
        soup = BeautifulSoup(normalized, "xml")

        self.assertEqual(soup.section.find("div"), None)
        self.assertNotIn("javascript:alert", normalized)
        self.assertFalse(soup.section.find("a").has_attr("href"))
        self.assertIn("<p>Line one</p>", normalized)

    def test_extract_author_from_chapters_reads_marked_author(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_chapter(
                root,
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><p class="author">Jan Kowalski</p></body></html>',
            )
            self._write_chapter(
                root,
                "chapter_002.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><h2>Other</h2></body></html>',
            )
            author = _extract_author_from_chapters([root / "chapter_001.xhtml", root / "chapter_002.xhtml"])
        self.assertEqual(author, "Jan Kowalski")

    def test_collect_reference_link_candidates_extracts_urls(self):
        text = "Reference links: https://example.com/path and also www.sample.org/help"
        candidates = _collect_reference_link_candidates(text)
        urls = {entry["normalized"] for entry in candidates}
        self.assertIn("https://example.com/path", urls)
        self.assertIn("https://www.sample.org/help", urls)
        self.assertEqual(len(candidates), 2)

    def test_classify_reading_flow_document_identifies_ai_note_and_caption_stub(self):
        with TemporaryDirectory() as temp_dir:
            ai_path = self._write_chapter(
                Path(temp_dir),
                "chapter_ai.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Chapter</title></head>"
                "<body><h1>Definicje</h1></body></html>",
            )
            caption_path = self._write_chapter(
                Path(temp_dir),
                "chapter_image.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Image</title></head>"
                "<body><img src='diagram.png'/></body></html>",
            )

            ai_class = _classify_reading_flow_document(ai_path)
            caption_class = _classify_reading_flow_document(caption_path)

        self.assertEqual(ai_class.kind, "ai_note")
        self.assertTrue(ai_class.remove_in_clean)
        self.assertEqual(caption_class.kind, "caption")
        self.assertTrue(caption_class.remove_in_clean)

    def test_classify_reading_flow_document_distinguishes_front_matter_by_page_length(self):
        with TemporaryDirectory() as temp_dir:
            front_short = self._write_chapter(
                Path(temp_dir),
                "front_short.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Contents</title></head>"
                "<body><h1>Contents</h1><p>Contents</p></body></html>",
            )
            front_long = self._write_chapter(
                Path(temp_dir),
                "front_long.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Contents</title></head><body><h1>Contents</h1>"
                "<p>" + "word " * 60 + "</p></body></html>",
            )

            short_class = _classify_reading_flow_document(front_short)
            long_class = _classify_reading_flow_document(front_long)

        self.assertEqual(short_class.kind, "front_matter")
        self.assertTrue(short_class.remove_in_clean)
        self.assertEqual(long_class.kind, "front_matter")
        self.assertFalse(long_class.remove_in_clean)

    def test_resolve_heading_target_falls_back_to_h1_when_title_is_front_cover(self):
        with TemporaryDirectory() as temp_dir:
            path = self._write_chapter(
                Path(temp_dir),
                "chapter.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Front Cover</title></head>'
                '<body><section id="front"><h1 id="title-id">Front Cover</h1></body></html>',
            )
            title, target = _resolve_heading_target(path)
        self.assertEqual(title, "Front Cover")
        self.assertEqual(target, "title-id")

    def test_resolve_heading_target_prefers_page_title_when_matching_primary_heading(self):
        with TemporaryDirectory() as temp_dir:
            path = self._write_chapter(
                Path(temp_dir),
                "chapter.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Real Title</title></head>'
                '<body><h1 id="chapter-title">Real Title</h1><h2 id="section-id">Real subtitle</h2></body></html>',
            )
            title, target = _resolve_heading_target(path)
        self.assertEqual(title, "Real Title")
        self.assertEqual(target, "chapter-title")

    def test_should_include_in_toc_rejects_short_and_high_level_labels(self):
        self.assertFalse(_should_include_in_toc("1.", 1))
        self.assertFalse(_should_include_in_toc("A", 4))
        self.assertTrue(_should_include_in_toc("Chapter 5: Planning", 1))

    def test_solution_title_match_score_covers_major_branches(self):
        self.assertEqual(_solution_title_match_score("Chapter 1: Example", "Example"), 90)
        self.assertEqual(_solution_title_match_score("Chapter 1: Example", "Chapter 1: Example"), 100)
        self.assertEqual(_solution_title_match_score("Alpha Beta", "Beta Alpha"), 25)
        self.assertEqual(_solution_title_match_score("Unrelated", "Different"), 0)

    def test_classify_magazine_feature_buckets_classifies_feature_files(self):
        chapter_infos = [
            {
                "file_name": "front.xhtml",
                "title": "Cover",
                "start_id": "front-id",
                "index": 0,
                "start_node_index": 0,
                "is_special_hint": False,
            },
            {
                "file_name": "feature.xhtml",
                "title": "Feature story",
                "start_id": "feature-id",
                "index": 2,
                "start_node_index": 1,
                "is_special_hint": False,
            },
            {
                "file_name": "special.xhtml",
                "title": "Special item",
                "start_id": "special-id",
                "index": 4,
                "start_node_index": 2,
                "is_special_hint": True,
            },
            {
                "file_name": "assigned.xhtml",
                "title": "Assigned section",
                "start_id": "assigned-id",
                "index": 7,
                "start_node_index": 3,
                "is_special_hint": False,
            },
        ]
        assignments = [
            {"file_name": "front.xhtml", "candidate_kind": "start", "chapter_index": 0},
            {"file_name": "assigned.xhtml", "candidate_kind": "regular", "chapter_index": 7},
        ]

        front_features, additional_features, extras = _classify_magazine_feature_buckets(
            chapter_infos,
            assignments,
            contents_file="contents.xhtml",
        )

        self.assertEqual([item["file_name"] for item in front_features], [])
        self.assertEqual(
            [item["file_name"] for item in additional_features],
            ["feature.xhtml", "assigned.xhtml"],
        )
        self.assertIn("special.xhtml", extras)

    def test_finalize_profile_helpers_are_case_normalized(self):
        self.assertEqual(_publication_profile_key("Kindle-Reading_01"), "kindle_reading_01")
        self.assertTrue(_is_clean_reading_profile("Kindle-Clean Reading"))
        self.assertTrue(_is_learning_mode_profile(" Magazine-Learning_Mode "))
        self.assertFalse(_is_learning_mode_profile("book"))

    def test_finalize_reference_report_helpers_cover_core_contracts(self):
        empty = _empty_reference_report()
        self.assertEqual(empty["sections_detected"], 0)
        self.assertEqual(_finalize_reference_report(None), empty)

        merged = {"sections_detected": 2, "records_detected": 1}
        _merge_reference_report(empty, merged)
        self.assertEqual(empty["sections_detected"], 2)
        self.assertEqual(empty["records_detected"], 1)

        gate = _gate_result("ok", blockers=["x"], warnings=["y"], details={"score": 10})
        self.assertEqual(gate["status"], "ok")
        self.assertEqual(gate["blockers"], ["x"])
        self.assertEqual(gate["warnings"], ["y"])
        self.assertEqual(gate["details"]["score"], 10)

    def test_finalize_relative_path_helper_uses_root_relative_output(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = self._write_chapter(root, "chapter.xhtml", "<html></html>")
        self.assertEqual(_safe_relative_path(nested, root), "chapter.xhtml")
        self.assertEqual(_safe_relative_path(Path(root).parent, root), Path(root).parent.as_posix())

    def test_plan_clean_reading_flow_keeps_fallback_when_all_chapters_filtered(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            short_1 = self._write_chapter(
                root,
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Contents</h1></body></html>",
            )
            short_2 = self._write_chapter(
                root,
                "chapter_002.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Contents</h1><p>a lot of text for fallback</p></body></html>",
            )
            active_paths, report = _plan_clean_reading_flow(
                [short_1, short_2],
                language="en",
                publication_profile="Kindle-Clean Reading",
            )

        self.assertEqual(len(active_paths), 1)
        self.assertEqual(active_paths[0], short_2)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["summary"]["removed_count"], 1)
        self.assertEqual(report["summary"]["inline_ai_note_block_count"], 0)

    def test_strip_inline_ai_note_blocks_keeps_pl_architektura_single_heading(self):
        with TemporaryDirectory() as temp_dir:
            path = self._write_chapter(
                Path(temp_dir),
                "chapter.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><h2>Architektura</h2>'
                "<p>Notatka AI: usunięcie tylko przy dużej liczbie znaczników</p></body></html>",
            )
            removed = _strip_inline_ai_note_blocks(path, source_language="pl")

        self.assertEqual(removed, [])

    def test_finalize_phase_report_initialization_has_expected_sections(self):
        report = _initialize_finalize_phase_report(
            title="Sample Title",
            author="Jane Doe",
            language="en",
            publication_profile="Kindle-Clean_Reading",
        )
        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["summary"], {})
        self.assertIn("B", report["gates"])
        self.assertEqual(report["input"]["publication_profile"], "Kindle-Clean_Reading")

    def test_finalize_report_mode_switches(self):
        self.assertTrue(_wants_rich_finalize_report("rich"))
        self.assertTrue(_wants_rich_finalize_report("phase"))
        self.assertFalse(_wants_rich_finalize_report("reference"))

    def test_strip_inline_ai_notes_keeps_no_body_safe(self):
        with TemporaryDirectory() as temp_dir:
            path = self._write_chapter(
                Path(temp_dir),
                "no-body.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head></html>',
            )
            removed = _strip_inline_ai_note_blocks(path, source_language="en")
        self.assertEqual(removed, [])

    def test_strip_inline_ai_notes_removes_ai_note_heading_and_text(self):
        with TemporaryDirectory() as temp_dir:
            path = self._write_chapter(
                Path(temp_dir),
                "notes.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h2>Definicje</h2>"
                "<p>Definicje i przykładowy opis</p>"
                "<p>Notatka AI: this is a synthetic inline marker</p>"
                "<p>Notatka AI: remove this line</p>"
                "</body></html>",
            )
            removed = _strip_inline_ai_note_blocks(path, source_language="en")
        self.assertTrue(removed)
        self.assertEqual(len(removed), 1)

    def test_plan_clean_reading_flow_skips_when_profile_not_clean(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_chapter(
                root,
                "chapter.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Regular article</h1></body></html>",
            )
            chapter = root / "chapter.xhtml"
            active_paths, report = _plan_clean_reading_flow(
                [chapter],
                language="en",
                publication_profile="standard",
            )
        self.assertEqual(active_paths, [chapter])
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["summary"]["removed_count"], 0)

    def test_detect_cleanup_scope_distinguishes_book_magazine_and_training(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            training_chapter = self._write_chapter(
                root,
                "chapter_training.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Easy Exercises</h1>"
                "<p>Training content with solutions page markers.</p></body></html>",
            )
            regular_chapter = self._write_chapter(
                root,
                "chapter_regular.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Chapter 1</h1>"
                "<p>Regular publishing article content.</p></body></html>",
            )
            magazine = self._write_chapter(
                root,
                "chapter_magazine.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Magazine title</h1>"
                "<p>Magazine section content.</p></body></html>",
            )

            self.assertEqual(
                _detect_cleanup_scope(
                    [training_chapter],
                    title="The Training Book",
                    publication_profile="book",
                ),
                "training-book",
            )
            self.assertEqual(
                _detect_cleanup_scope(
                    [magazine],
                    title="Magazine Title",
                    publication_profile="magazine_reflow",
                ),
                "magazine",
            )
            self.assertEqual(
                _detect_cleanup_scope(
                    [regular_chapter],
                    title="Reader Report",
                    publication_profile="kindle_reflow",
                ),
                "book",
            )

    def test_magazine_boundary_text_heuristic_handles_uppercase_chunks(self):
        self.assertTrue(_looks_like_magazine_boundary_text("NEWSROOM"))

    def test_merge_and_normalize_helpers_support_dictionary_and_key_roundtrip(self):
        report = _empty_reference_report()
        _merge_reference_report(
            report,
            {
                "sections_detected": 3,
                "records_detected": 2,
            },
        )
        self.assertEqual(report["sections_detected"], 3)
        self.assertEqual(report["records_detected"], 2)
        self.assertEqual(_normalize_key("  This   IS  a   Test "), "this is a test")

    def test_finalize_handles_pre_paginated_and_empty_spine_reports(self):
        container = """<?xml version='1.0' encoding='utf-8'?>
<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container' version='1.0'>
  <rootfiles><rootfile full-path='EPUB/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>"""
        pre_paginated_opf = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='bookid'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Pre-Paginated</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id='bookid'>urn:test:pre</dc:identifier>
    <meta property='rendition:layout'>pre-paginated</meta>
    <meta property='dcterms:modified'>2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id='chapter' href='chapter_001.xhtml' media-type='application/xhtml+xml'/>
    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml'/>
  </manifest>
  <spine>
    <itemref idref='chapter'/>
    <itemref idref='nav'/>
  </spine>
</package>"""
        chapter_source = "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>One</title></head><body><h1>One</h1></body></html>"
        empty_spine_opf = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='bookid'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Empty Spine</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id='bookid'>urn:test:empty</dc:identifier>
  </metadata>
  <manifest>
    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml'/>
  </manifest>
  <spine>
    <itemref idref='nav'/>
  </spine>
</package>"""
        nav_source = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Nav</title></head><body></body></html>"""

        epub_pre_paginated = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container,
                "EPUB/content.opf": pre_paginated_opf,
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": nav_source,
            }
        )
        _, pre_report = finalize_epub_for_kindle(
            epub_pre_paginated,
            title="Pre-Paginated",
            author="KindleMaster QA",
            language="en",
            return_report=True,
            report_mode="rich",
        )
        self.assertEqual(pre_report["summary"]["cleanup_scope"], "pre-paginated")
        self.assertEqual(pre_report["gates"]["F"]["status"], "skipped")

        epub_empty_spine = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container,
                "EPUB/content.opf": empty_spine_opf,
                "EPUB/nav.xhtml": nav_source,
            }
        )
        _, empty_report = finalize_epub_for_kindle(
            epub_empty_spine,
            title="Empty Spine",
            author="KindleMaster QA",
            language="en",
            return_report=True,
            report_mode="rich",
        )
        self.assertEqual(empty_report["summary"]["cleanup_scope"], "empty-spine")
        self.assertEqual(empty_report["gates"]["F"]["status"], "fail")
        self.assertEqual(empty_report["summary"]["chapter_count"], 0)

    def test_finalize_epub_for_kindle_reports_rich_cleanup_for_book_scope(self):
        container = """<?xml version='1.0' encoding='utf-8'?>
<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container' version='1.0'>
  <rootfiles><rootfile full-path='EPUB/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>"""
        content_opf = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='bookid'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Demo</dc:title><dc:creator>Demo Author</dc:creator><dc:language>en</dc:language>
    <dc:identifier id='bookid'>urn:test:book</dc:identifier>
  </metadata>
  <manifest>
    <item id='chapter_001' href='chapter_001.xhtml' media-type='application/xhtml+xml'/>
    <item id='chapter_002' href='chapter_002.xhtml' media-type='application/xhtml+xml'/>
    <item id='cover' href='cover.xhtml' media-type='application/xhtml+xml'/>
    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml'/>
    <item id='css' href='style/default.css' media-type='text/css'/>
  </manifest>
  <spine>
    <itemref idref='chapter_001'/>
    <itemref idref='chapter_002'/>
  </spine>
</package>"""
        nav_source = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Nav</title></head><body>
<nav epub:type='toc'><ol><li><a href='chapter_001.xhtml'>Chapter 1</a></li><li><a href='chapter_002.xhtml'>Chapter 2</a></li></ol></nav>
</body></html>"""
        epub = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container,
                "EPUB/content.opf": content_opf,
                "EPUB/chapter_001.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Chapter 1</title></head><body><h1>Chapter 1</h1><p>Some opening text.</p></body></html>",
                "EPUB/chapter_002.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Chapter 2</title></head><body><h1>Chapter 2</h1><p>More content.</p></body></html>",
                "EPUB/cover.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><body><section><h1>Cover</h1></section></body></html>",
                "EPUB/nav.xhtml": nav_source,
                "EPUB/style/default.css": "body { margin: 0; }",
            }
        )
        _, report = finalize_epub_for_kindle(
            epub,
            title="Demo",
            author="Demo Author",
            language="en",
            return_report=True,
            report_mode="rich",
        )

        self.assertEqual(report["summary"]["cleanup_scope"], "book")
        self.assertEqual(report["summary"]["chapter_count"], 2)
        self.assertEqual(report["gates"]["F"]["status"], "pass_with_review")
        self.assertGreaterEqual(report["summary"]["toc_entry_count_after"], 2)
        self.assertIn("pass_with_review", report["status"])
        self.assertEqual(report["reference_cleanup"]["sections_detected"], 0)
        self.assertEqual(report["reference_cleanup"]["records_detected"], 0)

    def test_finalize_epub_for_kindle_reports_training_book_cleanup_scope(self):
        container = """<?xml version='1.0' encoding='utf-8'?>
<container xmlns='urn:oasis:names:tc:opendocument:xmlns:container' version='1.0'>
  <rootfiles><rootfile full-path='EPUB/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>"""
        content_opf = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='bookid'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Training Demo</dc:title><dc:creator>Demo Author</dc:creator><dc:language>en</dc:language>
    <dc:identifier id='bookid'>urn:test:training</dc:identifier>
    <meta property='dcterms:modified'>2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id='chapter_001' href='chapter_001.xhtml' media-type='application/xhtml+xml'/>
    <item id='chapter_002' href='chapter_002.xhtml' media-type='application/xhtml+xml'/>
    <item id='chapter_003' href='chapter_003.xhtml' media-type='application/xhtml+xml'/>
    <item id='cover' href='cover.xhtml' media-type='application/xhtml+xml'/>
    <item id='nav' href='nav.xhtml' media-type='application/xhtml+xml'/>
    <item id='css' href='style/default.css' media-type='text/css'/>
  </manifest>
  <spine>
    <itemref idref='chapter_001'/>
    <itemref idref='chapter_002'/>
    <itemref idref='chapter_003'/>
  </spine>
</package>"""
        nav_source = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'><head><title>Nav</title></head><body>
<nav epub:type='toc'><ol>
  <li><a href='chapter_001.xhtml'>Easy Exercises</a></li>
  <li><a href='chapter_002.xhtml'>Solutions to easy exercises</a></li>
  <li><a href='chapter_003.xhtml'>Name Index</a></li>
</ol></nav>
</body></html>"""
        epub = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container,
                "EPUB/content.opf": content_opf,
                "EPUB/chapter_001.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Easy Exercises</title></head><body><h1>Easy exercises</h1>"
                "<figure class='chess-problem'><figcaption class='diagram-caption'>1. Karpov - Kramnik, 1970 - 2-0</figcaption></figure></body></html>",
                "EPUB/chapter_002.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Solutions to easy exercises</title></head><body>"
                "<h1>Solutions to easy exercises</h1><h3 class='solution-entry' id='solution-1'>1. Example solution 2024</h3></body></html>",
                "EPUB/chapter_003.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Name Index</title></head><body><h1>Name Index</h1></body></html>",
                "EPUB/cover.xhtml": "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Cover</title></head><body><section id='cover'><h1>Training Demo</h1></section></body></html>",
                "EPUB/nav.xhtml": nav_source,
                "EPUB/style/default.css": "body { margin: 0; }",
            }
        )
        _, report = finalize_epub_for_kindle(
            epub,
            title="Training Demo",
            author="Demo Author",
            language="en",
            return_report=True,
            report_mode="rich",
        )

        self.assertEqual(report["summary"]["cleanup_scope"], "training-book")
        self.assertEqual(report["summary"]["chapter_count"], 3)
        self.assertIn(report["gates"]["F"]["status"], {"pass", "pass_with_review"})
        self.assertGreaterEqual(report["summary"]["toc_entry_count_after"], 2)
        self.assertEqual(report["reference_cleanup"]["sections_detected"], 0)
        self.assertEqual(report["reference_cleanup"]["records_detected"], 0)

    def test_rebuild_reference_sections_builds_reference_list_items(self):
        report = _empty_reference_report()
        rebuilt = _rebuild_reference_sections(
            [
                {"type": "heading", "text": "References", "level": 1},
                {"type": "paragraph", "text": "[1] Example reference. 2025. https://example.com/resource", "class_name": ""},
            ],
            chapter_title="Chapter One",
            reference_report=report,
            reference_details=[],
        )
        self.assertEqual(rebuilt[0], {"type": "heading", "text": "References", "level": 1})
        self.assertEqual(rebuilt[1]["type"], "list-item")
        self.assertEqual(rebuilt[1]["list_kind"], "ol")
        self.assertEqual(report["sections_detected"], 1)
        self.assertEqual(report["records_detected"], 1)
        self.assertEqual(report["entries_rebuilt"], 1)
        self.assertEqual(report["scope_replaced_count"], 1)

    def test_rebuild_reference_sections_with_descriptors_and_url_targets(self):
        report = _empty_reference_report()
        rebuilt = _rebuild_reference_sections(
            [
                {"type": "heading", "text": "References", "level": 1},
                {"type": "paragraph", "text": "Kowalski, J. - Sample title, 2026"},
                {"type": "paragraph", "text": "https://example.com/reference-1"},
            ],
            chapter_title="References",
            reference_report=report,
            reference_details=[],
        )

        self.assertEqual(rebuilt[0]["type"], "heading")
        self.assertEqual(rebuilt[1]["type"], "list-item")
        self.assertIn("reference-entry", rebuilt[1]["class_name"])
        self.assertEqual(rebuilt[1]["list_kind"], "ul")
        self.assertEqual(report["sections_detected"], 1)
        self.assertEqual(report["records_detected"], 1)

    def test_rebuild_reference_sections_splits_entries_and_records_review_details(self):
        report = _empty_reference_report()
        details = []
        rebuilt = _rebuild_reference_sections(
            [
                {"type": "heading", "text": "References", "level": 2},
                {
                    "type": "paragraph",
                    "text": "[3] First source. https://example.com/a [5] Second source. https://example.com/b",
                    "class_name": "",
                },
                {"type": "paragraph", "text": "Not enough bibliographic data", "class_name": ""},
                {"type": "heading", "text": "Next chapter", "level": 2},
                {"type": "paragraph", "text": "Regular body text", "class_name": ""},
            ],
            chapter_title="Chapter with references",
            reference_report=report,
            reference_details=details,
        )

        reference_items = [block for block in rebuilt if block.get("type") == "list-item"]
        self.assertGreaterEqual(len(reference_items), 2)
        self.assertGreaterEqual(report["split_record_count"], 1)
        self.assertGreaterEqual(report["numbering_issue_count"], 1)
        self.assertGreaterEqual(report["clickable_link_count"], 2)
        self.assertEqual(len(details), len(reference_items))
        self.assertEqual(rebuilt[-1]["text"], "Regular body text")

    def test_extract_reference_entries_from_block_parses_reference_block_types(self):
        existing_entries = _extract_reference_entries_from_block(
            {
                "type": "list-item",
                "class_name": "reference-entry",
                "text": "[1] Example Reference Description https://example.com",
                "html": "<li class='reference-entry'><span class='reference-id'>[1]</span> <span class='reference-title'>Example Reference</span> <span class='reference-description'>Description</span> <a href='https://example.com'>https://example.com</a></li>",
            }
        )
        table_entries = _extract_reference_entries_from_block(
            {
                "type": "table",
                "rows": [["[2]", "Reference two", "www.example.org"], ["[3]", "Reference three", "https://example.com/three"]],
            }
        )
        self.assertEqual(len(existing_entries), 1)
        self.assertEqual(existing_entries[0]["source_id"], "[1]")
        self.assertEqual(len(table_entries), 2)
        self.assertEqual(table_entries[0]["source_id"], "[2]")
        self.assertEqual(table_entries[1]["source_id"], "[3]")

    def test_build_inventory_conflicts_collects_multiple_discrepancies(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                "<head><title>Book Source</title></head>"
                "<body><h1>Published Title</h1></body></html>",
            )
            conflicts = _build_inventory_conflicts(
                [chapter],
                metadata_snapshot={"title": "Different Book", "creator": "unknown", "language": "de"},
                requested_title="unknown",
                requested_author="Jan Kowalski",
                requested_language="en",
            )

        reasons = {item["reason"] for item in conflicts}
        self.assertIn("metadata-heading-conflict", reasons)
        self.assertIn("placeholder-author", reasons)
        self.assertIn("language-mismatch", reasons)
        self.assertIn("requested-title-technical", reasons)

    def test_extract_front_matter_title_candidate_finds_valid_title_candidate(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                "<head><title>Sample Story</title></head>"
                "<body><h1>Title</h1><h2>The Great Adventure Guide</h2></body></html>",
            )
            extracted = _extract_front_matter_title_candidate(chapter)

        self.assertEqual(extracted, "The Great Adventure Guide")

    def test_collect_heading_candidates_distinguishes_pseudo_vs_real(self):
        html = (
            "<html xmlns='http://www.w3.org/1999/xhtml'>"
            "<head><title>Cap</title></head>"
            "<body>"
            "<h1>Main Chapter</h1>"
            "<p class='heading'>Section One and Overview Details</p>"
            "</body></html>"
        )
        candidates_real = _collect_heading_candidates_from_text(
            html,
            file_name="chapter.xhtml",
            include_pseudo=False,
        )
        candidates_mixed = _collect_heading_candidates_from_text(
            html,
            file_name="chapter.xhtml",
            include_pseudo=True,
        )
        self.assertEqual(len(candidates_real), 1)
        self.assertEqual(len(candidates_mixed), 2)
        self.assertTrue(any(item["candidate_type"] == "pseudo" for item in candidates_mixed))

    def test_is_pseudo_heading_candidate_filters_non_heading_like_text(self):
        soup = BeautifulSoup(
            "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>1. First item</p><p>Section One and Overview</p></body></html>",
            "xml",
        )
        paragraph_nodes = soup.find_all("p")
        self.assertFalse(_is_pseudo_heading_candidate(paragraph_nodes[0], "1. First item"))
        self.assertTrue(_is_pseudo_heading_candidate(paragraph_nodes[1], "Section One and Overview"))

    def test_find_matching_and_classify_heading_candidates(self):
        after_candidates = [
            {"id": "h1", "text": "The main title", "level": 1},
            {"id": "h2", "text": "Secondary title", "level": 2},
        ]
        before = {"id": "h1", "text": "The main title"}
        best_index = _find_matching_heading_candidate(before, after_candidates)
        self.assertEqual(best_index, 0)
        self.assertIsNone(_find_matching_heading_candidate({"id": "missing", "text": "No match"}, []))

        self.assertEqual(
            _classify_heading_decision(
                before={"candidate_type": "pseudo", "text": "Pseudo heading", "level": 3},
                after={"id": "x", "text": "Pseudo heading", "level": 3},
                repeated_counts=Counter(),
            )[0],
            "promoted",
        )
        self.assertEqual(
            _classify_heading_decision(
                before={"candidate_type": "real", "text": "Ad block", "level": 2},
                after=None,
                repeated_counts=Counter({"Ad block": 4}),
            )[0],
            "removed",
        )
        self.assertEqual(
            _classify_heading_decision(
                before=None,
                after={"id": "main", "text": "Main", "level": 1},
                repeated_counts=Counter(),
            )[0],
            "added",
        )
        self.assertEqual(
            _classify_heading_decision(before=None, after=None, repeated_counts=Counter())[0],
            "unchanged",
        )

    def test_heading_candidate_layout_artifact_detects_repeated_or_marked_text(self):
        self.assertTrue(
            _heading_candidate_looks_like_layout_artifact(
                {"text": "Ad Banner"},
                repeated_counts=Counter({"Ad Banner": 4}),
            )
        )
        self.assertTrue(
            _heading_candidate_looks_like_layout_artifact(
                {"text": "This is an advertorial section"},
                repeated_counts=Counter(),
            )
        )
        self.assertFalse(
            _heading_candidate_looks_like_layout_artifact(
                {"text": "Chapter Summary"},
                repeated_counts=Counter(),
            )
        )

    def test_heading_review_item_suppression_rules(self):
        self.assertTrue(
            _should_suppress_heading_review_item(
                {
                    "reason": "ambiguous-heading-removed",
                    "before": {"text": "Deep Learning"},
                    "after": {},
                }
            )
        )
        self.assertTrue(
            _should_suppress_heading_review_item(
                {
                    "reason": "reconstructed-heading",
                    "before": {},
                    "after": {"text": "Summary"},
                }
            )
        )
        self.assertFalse(
            _should_suppress_heading_review_item(
                {"reason": "other", "before": {"text": "Any"}, "after": {"text": "Any"}}
            )
        )

    def test_dedupe_manual_review_items_removes_duplicates(self):
        deduped = _dedupe_manual_review_items(
            [
                {"phase": "A", "file": "x", "element": "e", "before": "a", "reason": "dup"},
                {"phase": "A", "file": "x", "element": "e", "before": "a", "reason": "dup"},
                {"phase": "A", "file": "x", "element": "e2", "before": "a", "reason": "dup"},
            ]
        )
        self.assertEqual(len(deduped), 2)

    def test_build_metadata_phase_report_marks_inconsistencies(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                "<head><title>Chapter</title></head><body><h1>Dominant Title</h1></body></html>",
            )
            phase_report = _build_metadata_phase_report(
                before={"title": "Old", "creator": "Old", "language": "de", "counts": {"modified": 0}},
                after={
                    "title": "Wrong title",
                    "creator": "unknown",
                    "language": "pl",
                    "counts": {"modified": 0},
                },
                requested_title="Sample",
                requested_author="Jan",
                requested_language="en",
                chapter_paths=[chapter],
            )
        reasons = {item["reason"] for item in phase_report["manual_review"]}
        self.assertIn("title-does-not-match-dominant-heading", reasons)
        self.assertIn("author-still-placeholder", reasons)
        self.assertIn("language-differs-from-requested", reasons)

    def test_build_toc_map_tracks_issue_tags(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = self._write_chapter(
                root,
                "chapter.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                "<body><h2 id='main'>Main Heading</h2></body></html>",
            )
            toc_map = _build_toc_map(
                [
                    {"text": "Main Heading", "file_name": "chapter.xhtml", "id": "main", "level": 1},
                    {"text": "Missing File", "file_name": "missing.xhtml", "id": "m", "level": 1},
                    {"text": "Missing Anchor", "file_name": "chapter.xhtml", "id": "x", "level": 1},
                    {"text": "Main Heading", "file_name": "chapter.xhtml", "id": "main", "level": 1},
                ],
                chapter_paths=[chapter],
                package_dir=root,
            )
        self.assertEqual(toc_map[0]["status"], "pass")
        self.assertEqual(toc_map[1]["status"], "fail")
        self.assertIn("missing-file", toc_map[1]["issues"])
        self.assertIn("missing-anchor", toc_map[2]["issues"])
        self.assertIn("duplicate-target", toc_map[3]["issues"])

    def test_toc_entries_follow_spine_order_reports_out_of_order(self):
        toc_entries = [{"file_name": "c2.xhtml"}, {"file_name": "c1.xhtml"}]
        self.assertFalse(_toc_entries_follow_spine_order(toc_entries, spine_order=["c1.xhtml", "c2.xhtml"]))
        self.assertTrue(_toc_entries_follow_spine_order(toc_entries, spine_order=["c2.xhtml", "c1.xhtml"]))

    def test_resolve_relative_href_collapses_parent_segments(self):
        self.assertEqual(_resolve_relative_href("folder/current.xhtml", "../other.xhtml"), "other.xhtml")
        self.assertEqual(_resolve_relative_href("folder/current.xhtml", "../folder2/x.html"), "folder2/x.html")

    def test_collect_structural_integrity_summary_reports_missing_and_broken_links(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_dir = root / "EPUB"
            package_dir.mkdir()
            opf_content = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Sample</dc:title><dc:creator>Auth</dc:creator><dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>"""
            (package_dir / "content.opf").write_text(opf_content, encoding="utf-8")
            (package_dir / "chapter.xhtml").write_text(
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<h1 id="a">A</h1>'
                '<h1 id="a">Duplicate id</h1>'
                '<a href="chapter.xhtml#missing-id">Broken anchor</a>'
                '<a href="missing.xhtml#x">Missing file</a>'
                "</body></html>",
                encoding="utf-8",
            )
            (package_dir / "nav.xhtml").write_text(
                "<html xmlns='http://www.w3.org/1999/xhtml'><body><nav epub:type='toc'><a href='chapter.xhtml#a'>A</a></nav></body></html>",
                encoding="utf-8",
            )
            summary = _collect_structural_integrity_summary(
                package_dir / "content.opf",
                root_dir=package_dir,
                chapter_paths=[package_dir / "chapter.xhtml"],
                toc_map=[],
            )
        self.assertEqual(summary["summary"]["duplicate_id_count"], 1)
        self.assertEqual(summary["summary"]["broken_internal_link_count"], 2)
        self.assertEqual(summary["summary"]["missing_manifest_file_count"], 0)

    def test_evaluate_gates_cover_blocking_and_review_paths(self):
        inv_gate = _evaluate_inventory_gate(
            spine_before={"items": [], "missing_manifest_refs": []},
            navigation_before={"toc_nav_count": 0, "nav_found": False, "warnings": []},
            chapter_count=0,
            pre_paginated=False,
        )
        self.assertEqual(inv_gate["status"], "fail")

        metadata_gate = _evaluate_metadata_gate(
            {
                "title": "Sample Book",
                "creator": "Jane Doe",
                "language": "en",
                "counts": {"modified": 1},
                "modified": "2026-01-01T00:00:00Z",
            }
        )
        self.assertEqual(metadata_gate["status"], "pass")

        heading_gate = _evaluate_heading_gate({"summary": {"chapters_with_multiple_h1": 0}})
        self.assertEqual(heading_gate["status"], "pass")

        toc_gate = _evaluate_toc_gate(
            {"after": {"toc_nav_count": 1}, "summary": {"broken_target_count": 0, "spine_order_matches": True}}
        )
        self.assertEqual(toc_gate["status"], "pass")

        structural_gate = _evaluate_structural_gate({"summary": {"duplicate_id_count": 0}})
        self.assertEqual(structural_gate["status"], "pass")

        release_gate = _evaluate_release_gate(
            {"B": metadata_gate, "C": heading_gate, "D": toc_gate, "E": structural_gate},
            manual_review_queue=[{"file": "x"}],
        )
        self.assertEqual(release_gate["status"], "pass_with_review")
        self.assertEqual(
            release_gate["warnings"],
            ["Manual review queue is not empty; external QA and EPUBCheck are still required."],
        )

    def test_collect_repeated_short_texts_tracks_short_top_level_paragraphs(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'>"
                "<body>"
                "<p>Short text A</p><p>Short text A</p>"
                "<h1>Top heading</h1><h2>Secondary</h2>"
                "<p>Other</p>"
                "</body></html>",
            )
            counts = _collect_repeated_short_texts([chapter])
        self.assertEqual(counts["Short text A"], 2)

    def test_process_chapter_with_no_body_returns_passthrough(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>No body</title></head></html>",
            )
            result = _process_chapter(
                chapter,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="No body",
                author="Tester",
                language="en",
            )
        self.assertEqual(result.nav_entries, [])
        self.assertEqual(result.solution_targets, {})

    def test_finalize_epub_for_kindle_returns_error_report_on_bad_epub_payload(self):
        _, report = finalize_epub_for_kindle(
            b"not-an-epub-payload",
            title="Corrupted",
            author="KindleMaster QA",
            language="en",
            return_report=True,
            report_mode="rich",
        )

        self.assertEqual(report["status"], "error")
        self.assertEqual(report["gates"]["F"]["status"], "fail")
        self.assertEqual(report["summary"]["chapter_count"], 0)
        self.assertIn("not a zip file", report["summary"]["error"])

    def test_extract_logical_blocks_generates_expected_types_and_repetition_heading(self):
        soup = BeautifulSoup(
            "<body>"
            "<h1>Strona 1</h1>"
            "<h2>Chapter section</h2>"
            "<p class='diagram-tail'>Some explanation</p>"
            "<ul><li>First</li><li>Second</li></ul>"
            "<p>Body paragraph</p>"
            "<span class='page-marker'/>"
            "<figure><img src='diagram.png'/><figcaption>Diagram</figcaption></figure>"
            "<table><tr><td>Cell</td></tr></table>"
            "</body>",
            "xml",
        ).body
        blocks = _extract_logical_blocks(
            list(soup.find_all(recursive=False)),
            repeated_counts=Counter(),
            keep_first_seen=set(),
            title="Book Title",
            author="Book Author",
            section_context="body",
        )

        self.assertEqual(blocks[0]["type"], "heading")
        self.assertEqual(blocks[0]["text"], "Chapter section")
        self.assertEqual(blocks[1]["type"], "paragraph")
        self.assertIn("diagram-tail", blocks[1]["class_name"])
        self.assertEqual(blocks[2]["type"], "list-item")
        self.assertEqual(blocks[3]["type"], "list-item")
        self.assertEqual(blocks[4]["type"], "paragraph")
        self.assertEqual(blocks[5]["type"], "page-marker")
        self.assertEqual(blocks[6]["type"], "figure")
        self.assertEqual(blocks[7]["type"], "table")

        repeated_blocks = _extract_logical_blocks(
            [BeautifulSoup("<p>Solutions to easy exercises</p>", "xml").p],
            repeated_counts=Counter({"Solutions to easy exercises": 5}),
            keep_first_seen=set(),
            title="Book Title",
            author="Book Author",
            section_context="body",
        )
        self.assertEqual(len(repeated_blocks), 1)
        self.assertEqual(repeated_blocks[0]["type"], "heading")
        self.assertEqual(repeated_blocks[0]["level"], 1)

    def test_extract_logical_blocks_handles_solution_markers_and_links(self):
        soup = BeautifulSoup(
            "<body>"
            "<p id='exercise-12'>12</p>"
            "<p>support@example.com</p>"
            "<p><a class='solution-backlink' href='chapter_002.xhtml#solution-3'>3. Karpov - Kramnik</a></p>"
            "<p><a class='solution-backlink' href='chapter_002.xhtml#note'>Back to note</a></p>"
            "<p><a href='chapter_010.xhtml'>Solutions page 10</a></p>"
            "<p class='author'>by Jane Author</p>"
            "<p class='subtitle'>A compact subtitle</p>"
            "</body>",
            "xml",
        ).body

        blocks = _extract_logical_blocks(
            list(soup.find_all(recursive=False)),
            repeated_counts=Counter(),
            keep_first_seen=set(),
            title="Training Book",
            author="Jane Author",
            section_context="body",
        )

        types = [block["type"] for block in blocks]
        self.assertIn("exercise-marker", types)
        self.assertIn("solution-heading", types)
        self.assertIn("problem-page-link", types)
        self.assertFalse(any(block.get("text") == "support@example.com" for block in blocks))
        solution = next(block for block in blocks if block["type"] == "solution-heading")
        self.assertEqual(solution["exercise_num"], "3")
        self.assertEqual(solution["target"], "chapter_002.xhtml#solution-3")
        self.assertTrue(any(block.get("class_name") == "author" for block in blocks))
        self.assertTrue(any(block.get("class_name") == "subtitle" for block in blocks))

    def test_process_chapter_flattens_wrappers_and_strips_redundant_title_fragments(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Main Chapter</title></head>"
                "<body><section>"
                "<p>Main Chapter</p>"
                "<h2>First section</h2>"
                "<p>Intro text</p>"
                "</section></body></html>",
            )
            result = _process_chapter(
                chapter,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Main Chapter",
                author="Tester",
                language="en",
            )

        self.assertIn("First section", result.xhtml)
        self.assertNotIn("<p>Main Chapter</p>", result.xhtml)
        self.assertTrue(any(entry["text"] == "First section Intro text" for entry in result.nav_entries))
        self.assertEqual(result.solution_targets, {})

    def test_inject_problem_solution_links_uses_solution_targets_and_cleans_links(self):
        xhtml = (
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<figure class='chess-problem'><figcaption>1. Position</figcaption></figure>"
            "<figure class='chess-problem' id='exercise-2'><figcaption>2. Another position</figcaption></figure>"
            "<p class='problem-page-link'><a href='page.html'>Solution page</a></p>"
            "</body></html>"
        )
        output = _inject_problem_solution_links(
            xhtml,
            chapter_name="chapter_001.xhtml",
            solution_targets={"2": "chapter_002.xhtml#solution-2"},
            ordered_problem_refs=[
                {
                    "problem_file": "chapter_001.xhtml",
                    "exercise_num": "1",
                    "solution_href": "chapter_002.xhtml#solution-1",
                },
                {
                    "problem_file": "chapter_001.xhtml",
                    "exercise_num": "2",
                    "solution_href": "chapter_002.xhtml#solution-2",
                },
            ],
        )
        soup = BeautifulSoup(output, "xml")
        links = soup.find_all("p", class_="problem-solution-link")
        self.assertEqual(len(links), 2)
        self.assertIsNotNone(soup.find("a", {"href": "chapter_002.xhtml#solution-1"}))
        self.assertIsNone(soup.find("p", class_="problem-page-link"))

    def test_inject_problem_solution_links_handles_markers_and_missing_body(self):
        unchanged = _inject_problem_solution_links(
            "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>No body</title></head></html>",
            chapter_name="chapter_001.xhtml",
            solution_targets={"5": "chapter_002.xhtml#solution-5"},
            ordered_problem_refs=[],
        )
        self.assertIn("No body", unchanged)

        xhtml = (
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<p id='exercise-5' class='exercise-marker'>5</p>"
            "<p class='problem-page-link'><a href='solutions.xhtml'>Solutions page</a></p>"
            "<p class='diagram-tail'>12</p>"
            "</body></html>"
        )
        output = _inject_problem_solution_links(
            xhtml,
            chapter_name="chapter_001.xhtml",
            solution_targets={"5": "chapter_002.xhtml#solution-5"},
            ordered_problem_refs=[
                {
                    "problem_file": "chapter_001.xhtml",
                    "exercise_num": "5",
                    "solution_href": "chapter_002.xhtml#solution-5",
                }
            ],
        )
        soup = BeautifulSoup(output, "xml")
        marker = soup.find("p", class_="exercise-marker")
        self.assertIsNotNone(marker)
        self.assertEqual(marker.find_next_sibling("p", class_="problem-solution-link").a["href"], "chapter_002.xhtml#solution-5")
        self.assertIsNone(soup.find("p", class_="problem-page-link"))
        self.assertIsNone(soup.find("p", class_="diagram-tail"))

    def test_build_magazine_chapter_info_detects_gallery_and_candidates(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Gallery: issue one</title></head>"
                "<body><section>"
                "<h1>Gallery: issue one</h1>"
                "<p>n tekst Jan Kowalski</p>"
                "<h2>THIS IS A VERY LONG UPDATE</h2>"
                "</section></body></html>",
            )
            info = _build_magazine_chapter_info(chapter, index=0)

        self.assertEqual(info["file_name"], "chapter_001.xhtml")
        self.assertEqual(info["title_key"], "gallery: issue one")
        self.assertEqual(info["special_type"], "gallery")
        self.assertIn("candidates", info)
        kinds = {entry["kind"] for entry in info["candidates"]}
        self.assertIn("start", kinds)
        self.assertIn("byline", kinds)
        self.assertIn("heading", kinds)

    def test_derive_package_metadata_rewrites_technical_title_and_infers_author(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter_one = self._write_chapter(
                root,
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                "<h1>Great Book Title</h1>"
                "<p class='author'>Jane Doe</p>"
                "</body></html>",
            )
            chapter_two = self._write_chapter(
                root,
                "chapter_002.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                "<h2>Chapter introduction</h2>"
                "</body></html>",
            )
            resolved = _derive_package_metadata(
                [chapter_one, chapter_two],
                title="python-docx conversion",
                author="kindlemaster",
                language="en",
            )

        self.assertEqual(resolved[0], "Great Book Title")
        self.assertEqual(resolved[1], "Jane Doe")
        self.assertEqual(resolved[2], "en")

    def test_trim_trailing_nonessential_figures_removes_trailing_noise(self):
        section = BeautifulSoup(
            "<section>"
            "<figure class='chess-problem'><img src='dup.png'/></figure>"
            "<figure class='photo'><img src='dup.png'/></figure>"
            "<figure class='chess-problem'><img src='dup.png'/></figure>"
            "<p>  </p>"
            "</section>",
            "xml",
        ).section
        changed = _trim_trailing_nonessential_figures(section)
        self.assertTrue(changed)
        figures = list(section.find_all("figure"))
        self.assertEqual(len(figures), 1)
        self.assertIn("chess-problem", figures[0].get("class", []))

    def test_process_chapter_removes_redundant_title_paragraph(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'>"
                "<head><title>Chapter Title</title></head>"
                "<body><p>Chapter Title</p><h1>Chapter Title</h1><p>Next</p></body></html>",
            )
            result = _process_chapter(
                chapter,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Chapter Title",
                author="Tester",
                language="en",
            )
        self.assertNotIn("<p>Chapter Title</p>", result.xhtml)
        self.assertIn("<h2 id=\"next\">Next</h2>", result.xhtml)

    def test_process_chapter_renders_knowledge_sections_from_dense_explanatory_text(self):
        knowledge_text = (
            "Concept: semantic cleanup means rebuilding reading structure from noisy extraction while preserving valid EPUB semantics. "
            "How it works: first the pipeline detects headings, then it normalizes lists, then it rebuilds reader-friendly blocks, finally it keeps risky edits reviewable. "
            "For example, a damaged PDF export can become navigable when headings, examples, and business notes are separated into stable sections. "
            "Business impact: this reduces manual QA cost, improves Kindle readability, and lowers release risk for long technical publications."
        )
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_knowledge.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>Semantic Cleanup</title></head>"
                f"<body><h1>Semantic Cleanup</h1><p>{knowledge_text}</p></body></html>",
            )
            result = _process_chapter(
                chapter,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Semantic Cleanup",
                author="Tester",
                language="en",
            )

        self.assertIn("knowledge-body", result.xhtml)
        self.assertIn("knowledge-point", result.xhtml)
        self.assertTrue(any("How it works" in entry["text"] or "Concept" in entry["text"] for entry in result.nav_entries))

    def test_expand_semantic_blocks_builds_definitions_tables_quotes_and_inline_lists(self):
        expanded = _expand_semantic_blocks(
            [
                {
                    "type": "paragraph",
                    "text": "Term Alpha: A durable explanation for the first term. Term Beta: A durable explanation for the second term.",
                    "html": "Term Alpha: A durable explanation for the first term.<br/>Term Beta: A durable explanation for the second term.",
                    "class_name": "",
                },
                {
                    "type": "paragraph",
                    "text": "Name | Score Alice | 10 Bob | 11",
                    "html": "Name | Score<br/>Alice | 10<br/>Bob | 11",
                    "class_name": "",
                },
                {
                    "type": "paragraph",
                    "text": "First clear item; Second clear item; Third clear item",
                    "html": "First clear item; Second clear item; Third clear item",
                    "class_name": "",
                },
                {
                    "type": "paragraph",
                    "text": "\"This quoted sentence should become a block quote for reader clarity because it is long enough to look like an extracted quotation rather than a short inline phrase in a regular paragraph.\"",
                    "html": "\"This quoted sentence should become a block quote for reader clarity because it is long enough to look like an extracted quotation rather than a short inline phrase in a regular paragraph.\"",
                    "class_name": "",
                },
            ],
            section_context="body",
        )

        types = [block["type"] for block in expanded]
        self.assertIn("definition-list", types)
        self.assertIn("table", types)
        self.assertIn("list-item", types)
        self.assertIn("blockquote", types)

    def test_classify_frontmatter_signature_blocks_marks_frontmatter_signature_lines(self):
        blocks = [
            {"type": "heading", "text": "Jan Kowalski", "class_name": "", "level": 3},
            {"type": "paragraph", "text": "by Jan Nowak", "class_name": ""},
            {"type": "paragraph", "text": "Warsaw, 2024", "class_name": ""},
        ]
        output = _classify_frontmatter_signature_blocks(
            blocks,
            chapter_title="Introduction",
            section_context="body",
        )

        self.assertEqual(output[0]["type"], "paragraph")
        self.assertIn("signature", output[0]["class_name"])
        self.assertIn("signature", output[1]["class_name"])
        self.assertIn("dateline", output[2]["class_name"])

    def test_classify_frontmatter_signature_blocks_ignores_non_frontmatter_sections(self):
        blocks = [{"type": "paragraph", "text": "by Jan Nowak", "class_name": ""}]
        output = _classify_frontmatter_signature_blocks(
            blocks,
            chapter_title="Chapter One",
            section_context="body",
        )
        self.assertEqual(output, blocks)

    def test_apply_magazine_assignments_rewrites_nodes_and_inserts_headings(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter = self._write_chapter(
                root,
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<section>"
                "<p>Old intro</p>"
                "<h3>Legacy section</h3>"
                "<p>Tail paragraph</p>"
                "</section></body></html>",
            )
            chapter_info = {"path": str(chapter)}
            assignments = [
                {
                    "node_index": 0,
                    "label": "Magazine lead",
                    "candidate_kind": "start",
                    "override_start_title": False,
                    "promote_existing_heading": False,
                },
                {
                    "node_index": 1,
                    "label": "Legacy heading",
                    "candidate_kind": "heading",
                    "override_start_title": False,
                    "promote_existing_heading": True,
                },
                {
                    "node_index": 2,
                    "label": "Inserted heading",
                    "candidate_kind": "heading",
                    "override_start_title": False,
                    "promote_existing_heading": False,
                },
            ]
            _apply_magazine_assignments(chapter_info, assignments)

            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            self.assertEqual(soup.section.find("h1").name, "h1")
            self.assertEqual(soup.section.find_all("h2")[0].get("id"), assignments[1]["id"])
            self.assertEqual(soup.section.find_all("h2")[1].get("id"), assignments[2]["id"])
            self.assertEqual(soup.find("h2", string="Inserted heading").name, "h2")

    def test_repair_magazine_package_builds_issue_toc_and_rewrites_article_headings(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contents = self._write_chapter(
                root,
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Contents</title></head><body><section>'
                "<h1>Contents</h1>"
                "<p>Features</p>"
                "<p>12. Feature Story</p>"
                "<p>14. Second Story</p>"
                "</section></body></html>",
            )
            article_one = self._write_chapter(
                root,
                "chapter_002.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Feature Story</title></head><body><section>'
                '<h1 id="feature-story">Feature Story</h1>'
                "<p>n tekst Jane Reporter</p>"
                "<p>Body copy for the first article.</p>"
                "</section></body></html>",
            )
            article_two = self._write_chapter(
                root,
                "chapter_003.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Second Story</title></head><body><section>'
                '<h1 id="second-story">Second Story</h1>'
                "<p>Body copy for the second article.</p>"
                "</section></body></html>",
            )

            package = _repair_magazine_package(
                [contents, article_one, article_two],
                title="Magazine Demo",
                author="Editorial Team",
                language="en",
            )

            toc_texts = [entry["text"] for entry in package["toc_entries"]]
            self.assertEqual(package["title"], "Magazine Demo")
            self.assertIn("Table of Contents", toc_texts)
            self.assertIn("Articles", toc_texts)
            self.assertIn("Feature Story", toc_texts)
            self.assertIn("Second Story", toc_texts)
            self.assertIn("chapter_002.xhtml", package["spine_order"])

    def test_find_fallback_magazine_boundary_skips_non_start_candidates_after_slot_usage(self):
        chapter_infos = [
            {
                "file_name": "chapter_001.xhtml",
                "is_contents": False,
                "special_type": "",
                "has_byline": True,
                "start_node_index": 1,
                "candidates": [
                    {
                        "file_name": "chapter_001.xhtml",
                        "chapter_index": 0,
                        "node_index": 2,
                        "kind": "heading",
                    },
                    {
                        "file_name": "chapter_001.xhtml",
                        "chapter_index": 0,
                        "node_index": 5,
                        "kind": "start",
                    },
                ],
            }
        ]
        boundary = _find_fallback_magazine_boundary(
            chapter_infos,
            used_slots={("chapter_001.xhtml", 1)},
            start_after=(0, 1),
            stop_before=(1, 0),
        )

        self.assertIsNotNone(boundary)
        self.assertEqual(boundary["kind"], "start")
        self.assertEqual(boundary["node_index"], 5)

    def test_collect_fallback_game_targets_returns_unique_targets_and_adds_ids(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unique = self._write_chapter(
                root,
                "chapter_001.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                "<h2 id='game-unique'>Karpov - Kramnik, 1970 - 2-0</h2>"
                "</body></html>",
            )
            ambiguous_a = self._write_chapter(
                root,
                "chapter_002.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                "<h2>Karpov - Kramnik, 1971 - 2-0</h2>"
                "</body></html>",
            )
            ambiguous_b = self._write_chapter(
                root,
                "chapter_003.xhtml",
                "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                "<h2>Karpov - Kramnik, 1970 - 2-0</h2>"
                "</body></html>",
            )

            targets_unique = _collect_fallback_game_targets([unique])
            targets_ambiguous = _collect_fallback_game_targets([ambiguous_a, ambiguous_b])

            self.assertEqual(targets_unique, {"karpov - kramnik": "chapter_001.xhtml#game-unique"})
            self.assertNotIn("karpov - kramnik", targets_ambiguous)

            ambiguous_soup = BeautifulSoup(ambiguous_a.read_text(encoding="utf-8"), "xml")
            heading_id = ambiguous_soup.find("h2").get("id", "")
            self.assertTrue(heading_id.startswith("game-ref-"))

    def test_rewrite_solution_backlinks_adds_or_unwraps_links(self):
        with_target = _rewrite_solution_backlinks(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><h3 class="solution-entry" id="solution-1">1. Match</h3></body></html>',
            exercise_problem_targets={"1": "chapter_001.xhtml#ex-1"},
            expected_problem_file="chapter_001.xhtml",
            fallback_game_targets={},
        )
        soup_with = BeautifulSoup(with_target, "xml")
        link_with = soup_with.find("a", class_="solution-backlink")
        self.assertIsNotNone(link_with)
        self.assertEqual(link_with.get("href"), "chapter_001.xhtml#ex-1")

        without_target = _rewrite_solution_backlinks(
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<h3 class="solution-entry" id="solution-2">'
            '<a class="solution-backlink" href="chapter_999.xhtml#wrong">Wrong</a>'
            "</h3></body></html>",
            exercise_problem_targets={"2": "chapter_001.xhtml#ex-2"},
            expected_problem_file="chapter_010.xhtml",
            fallback_game_targets={},
        )
        soup_without = BeautifulSoup(without_target, "xml")
        self.assertIsNone(soup_without.find("a", class_="solution-backlink"))

    def test_cleanup_solution_chapter_removes_invalid_entries_and_banner_headings(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_010.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h1>Solutions to easy exercises</h1>"
                '<h2>Solutions to easy exercises</h2>'
                '<h3 class="solution-entry" id="solution-1">1. Good 2024</h3>'
                '<h3 class="solution-entry" id="solution-2">2. Bad heading</h3>'
                "<p>Tail</p>"
                "</body></html>",
            )
            _cleanup_solution_chapter(chapter)

            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            self.assertIsNone(soup.find("h2"))
            solution_headings = soup.find_all("h3", class_="solution-entry")
            self.assertEqual(len(solution_headings), 1)
            self.assertEqual(solution_headings[0]["id"], "solution-1")

    def test_repair_exercise_chapter_adds_problem_links_and_removes_noise(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h1>Easy Exercises</h1>"
                "<h2>Solutions page 1</h2>"
                '<h3 class="solution-entry" id="solution-9">9. Something</h3>'
                '<p>2023</p>'
                '<figure class="chess-problem"><figcaption class="diagram-caption">1. Karpov - Kramnik, 1970 - 2-0</figcaption></figure>'
                '<figure class="chess-problem" id="exercise-2"><figcaption class="diagram-caption">Game title</figcaption>'
                '<p class="problem-solution-link"><a href="bad.xhtml">bad</a></p></figure>'
                "</body></html>",
            )

            output = _repair_exercise_chapter(
                chapter,
                solution_targets={"1": "chapter_002.xhtml#solution-1"},
                solution_titles={},
            )

            self.assertEqual(output, {"1": "chapter_001.xhtml#exercise-1"})
            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            self.assertEqual(soup.find("h2"), None)
            self.assertEqual(soup.find("a", href="chapter_002.xhtml#solution-1").string, "Go to solution 1")
            self.assertEqual(len(soup.find_all("p", class_="problem-solution-link")), 1)
            self.assertIsNone(soup.find("figure", id="exercise-2").find("p", class_="problem-solution-link"))

    def test_repair_exercise_chapter_chooses_best_duplicate_and_rewrites_existing_link(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_004.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><section>'
                "<h1>Easy Exercises</h1>"
                "<p>Solutions page 4</p>"
                '<figure class="chess-problem" id="stale-id">'
                '<figcaption class="diagram-caption">4. Short</figcaption>'
                '<p class="problem-solution-link"><a href="old.xhtml">Old</a></p>'
                "</figure>"
                '<figure class="chess-problem" id="exercise-4">'
                '<figcaption class="diagram-caption">4. Karpov - Kramnik, 1970 - 1-0</figcaption>'
                '<p class="problem-solution-link"><a href="wrong.xhtml">Wrong label</a></p>'
                "</figure>"
                '<figure class="photo"><img src="tail.png"/></figure>'
                "</section></body></html>",
            )

            targets = _repair_exercise_chapter(
                chapter,
                solution_targets={"4": "chapter_010.xhtml#solution-4"},
                solution_titles={"4": "Karpov - Kramnik, 1970 - 1-0"},
            )

            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            best = soup.find("figure", id="exercise-4")
            duplicate = soup.find("figure", id="stale-id")
            self.assertEqual(targets, {"4": "chapter_004.xhtml#exercise-4"})
            self.assertIsNotNone(best)
            self.assertIsNone(duplicate)
            self.assertEqual(best.find("a")["href"], "chapter_010.xhtml#solution-4")
            self.assertEqual(best.find("a").get_text(" ", strip=True), "Go to solution 4")
            self.assertIsNone(soup.find("p", string="Solutions page 4"))

    def test_repair_name_index_chapter_promotes_title_and_demotes_numeric_headings(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_020.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><section>'
                "<p>Name Index</p>"
                "<h2>12</h2>"
                "<h2>Kasparov</h2>"
                "</section></body></html>",
            )

            _repair_name_index_chapter(chapter)

            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            self.assertEqual(soup.find("h1", id="name-index").get_text(" ", strip=True), "Name Index")
            self.assertIsNotNone(soup.find("p", string="12"))
            self.assertIsNotNone(soup.find("h2", string="Kasparov"))

    def test_repair_symbol_key_chapter_replaces_intro_with_semantic_legend(self):
        with TemporaryDirectory() as temp_dir:
            chapter = self._write_chapter(
                Path(temp_dir),
                "chapter_symbols.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><section>'
                "<p>Original symbol key dump</p>"
                "</section></body></html>",
            )

            _repair_symbol_key_chapter(chapter)

            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            legend = soup.find("ul", class_="symbol-legend")
            self.assertIsNotNone(legend)
            self.assertGreaterEqual(len(legend.find_all("li", class_="symbol-legend-item")), 10)
            self.assertIsNone(soup.find("p", string="Original symbol key dump"))

    def test_audit_diagram_presentation_adds_alt_classes_and_enhances_low_res_chess_asset(self):
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover - depends on optional local imaging stack
            self.skipTest(f"PIL unavailable: {exc}")

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "diagram.png"
            Image.new("RGB", (12, 10), "white").save(image_path)
            chapter = self._write_chapter(
                root,
                "chapter_diagram.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><section>'
                '<figure class="chess-problem"><img class="chess-diagram" src="diagram.png" alt="Chess diagram"/>'
                '<figcaption>Position after 12...Nf6</figcaption></figure>'
                "</section></body></html>",
            )

            _audit_diagram_presentation(root, language="en", chess_min_long_edge=24, chess_palette_colors=4)

            soup = BeautifulSoup(chapter.read_text(encoding="utf-8"), "xml")
            figure = soup.find("figure")
            image = soup.find("img")
            self.assertIn("detail-diagram", figure.get("class", []))
            self.assertEqual(image.get("alt"), "Position after 12...Nf6")
            with Image.open(image_path) as audited_image:
                self.assertGreaterEqual(max(audited_image.size), 24)

    def test_repair_training_book_package_rewrites_exercise_solution_linking(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            exercise = self._write_chapter(
                root,
                "chapter_001.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h1>Easy exercises</h1>"
                '<figure class="chess-problem"><figcaption class="diagram-caption">1. Example game</figcaption></figure>'
                "</body></html>",
            )
            solution = self._write_chapter(
                root,
                "chapter_002.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h1>Solutions to easy exercises</h1>"
                '<h3 class="solution-entry" id="solution-1">1. Example solution 2024</h3>'
                "</body></html>",
            )
            appendix = self._write_chapter(
                root,
                "chapter_003.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<h1>Appendix</h1>"
                "</body></html>",
            )

            report = _repair_training_book_package(
                [exercise, solution, appendix],
                title="Book title",
                author="Author Name",
                language="en",
            )

            self.assertEqual(report["title"], "Book title")
            self.assertEqual(report["author"], "Author Name")
            self.assertEqual(report["language"], "en")
            self.assertIsInstance(report["toc_entries"], list)
            self.assertTrue(report["toc_entries"])
            solution_soup = BeautifulSoup(solution.read_text(encoding="utf-8"), "xml")
            self.assertIsNotNone(solution_soup.find("a", class_="solution-backlink"))


if __name__ == "__main__":
    unittest.main()
