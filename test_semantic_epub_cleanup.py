import base64
import io
import re
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import etree

from kindle_semantic_cleanup import (
    _extract_reference_entries_from_block,
    _build_nav_xhtml,
    _build_toc_ncx,
    _derive_package_metadata,
    _expand_semantic_blocks,
    _inject_problem_solution_links,
    _looks_like_training_book,
    _normalize_text_light,
    _process_chapter,
    _rebuild_toc_entries_from_final_chapters,
    _repair_exercise_chapter,
    _should_include_in_toc,
    finalize_epub_for_kindle,
)


class SemanticEpubCleanupTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, bytes | str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                payload = content.encode("utf-8") if isinstance(content, str) else content
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, payload, compress_type=compress_type)
        return output.getvalue()

    def test_expand_semantic_blocks_rebuilds_lists_and_structured_blocks(self):
        blocks = [
            {
                "type": "paragraph",
                "text": "1. Detect headings 2. Rebuild lists 3. Expand navigation",
                "html": "1. Detect headings 2. Rebuild lists 3. Expand navigation",
                "class_name": "",
            },
            {
                "type": "paragraph",
                "text": "Scope: Defines the semantic pass; Output: Rebuilds EPUB markup; Owner: Controls navigation quality",
                "html": "Scope: Defines the semantic pass; Output: Rebuilds EPUB markup; Owner: Controls navigation quality",
                "class_name": "",
            },
            {
                "type": "paragraph",
                "text": "Label | Value Header Value",
                "html": "Label | Value<br/>Heading | EPUB<br/>Depth | H1-H3",
                "class_name": "",
            },
        ]

        expanded = _expand_semantic_blocks(blocks)

        self.assertEqual([block["type"] for block in expanded[:3]], ["list-item", "list-item", "list-item"])
        self.assertTrue(all(block.get("list_kind") == "ol" for block in expanded[:3]))
        self.assertEqual(expanded[3]["type"], "definition-list")
        self.assertEqual(len(expanded[3]["items"]), 3)
        self.assertEqual(expanded[4]["type"], "table")
        self.assertEqual(expanded[4]["headers"], ["Label", "Value"])

    def test_expand_semantic_blocks_rebuilds_semicolon_lists_and_column_tables(self):
        blocks = [
            {
                "type": "paragraph",
                "text": "Upload PDF; Analyze structure; Export EPUB",
                "html": "Upload PDF; Analyze structure; Export EPUB",
                "class_name": "",
            },
            {
                "type": "paragraph",
                "text": "Header Value Status Ready",
                "html": "Header\tValue<br/>Status\tReady<br/>Depth\tH2/H3",
                "class_name": "",
            },
        ]

        expanded = _expand_semantic_blocks(blocks)

        self.assertEqual([block["type"] for block in expanded[:3]], ["list-item", "list-item", "list-item"])
        self.assertTrue(all(block.get("list_kind") == "ul" for block in expanded[:3]))
        self.assertEqual(expanded[3]["type"], "table")
        self.assertEqual(expanded[3]["headers"], ["Header", "Value"])
        self.assertEqual(expanded[3]["rows"][0], ["Status", "Ready"])

    def test_process_chapter_emits_ordered_lists_definition_lists_and_multi_level_nav(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Semantic Reconstruction</title>
  </head>
  <body>
    <h1>Semantic Reconstruction</h1>
    <p>This introduction explains why strong EPUB semantics matter for quick navigation, stable anchors, and predictable reader behavior across Kindle devices.</p>
    <h2>Key Sections</h2>
    <p>1. Detect headings 2. Rebuild lists 3. Expand navigation</p>
    <h3>Field Mapping</h3>
    <p>Name: Section title; Target: Anchor id; Depth: Heading level</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_001.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Semantic Reconstruction",
                author="Tester",
                language="en",
            )

        self.assertIn("<ol>", result.xhtml)
        self.assertIn("<dl", result.xhtml)
        self.assertGreaterEqual(len(result.nav_entries), 3)
        self.assertEqual([entry["level"] for entry in result.nav_entries[:3]], [1, 2, 3])
        self.assertEqual([entry["text"] for entry in result.nav_entries[:3]], ["Semantic Reconstruction", "Key Sections", "Field Mapping"])

    def test_process_chapter_rebuilds_knowledge_sections_for_scannable_navigation(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Platforma integracyjna</title>
  </head>
  <body>
    <h1>Platforma integracyjna</h1>
    <p>Architektura platformy integracyjnej jest to warstwa orkiestracji, która porządkuje przepływ danych między modułami OCR, walidacji i publikacji EPUB. Najpierw pipeline pobiera plik, następnie analizuje strukturę PDF, potem buduje model semantyczny i na końcu renderuje paczkę EPUB. Na przykład ten sam mechanizm potrafi przełączyć profil z książki na magazyn bez zmiany interfejsu operatora. Dzięki temu zespół skraca czas przygotowania wydania, obniża liczbę ręcznych poprawek i szybciej dostarcza gotowy artefakt do czytnika.</p>
    <p>Zależności systemowe obejmują OCR, magazyn plików, kolejkę zadań i walidator EPUBCheck. System wymaga stabilnego API do pobierania dokumentów, pamięci na obrazy pośrednie i zgodnej wersji narzędzi Kindle. W praktyce awaria kolejki lub walidatora zatrzymuje publikację i wymaga ponownego uruchomienia procesu przez operatora.</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_knowledge.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Platforma integracyjna",
                author="Tester",
                language="pl",
            )

        self.assertIn(">Architektura</h2>", result.xhtml)
        self.assertIn(">Co to jest</h3>", result.xhtml)
        self.assertIn(">Jak działa</h3>", result.xhtml)
        self.assertIn(">Przykład</h3>", result.xhtml)
        self.assertIn(">Implikacje biznesowe</h3>", result.xhtml)
        self.assertIn(">Zależności systemowe</h2>", result.xhtml)
        self.assertIn("<ol>", result.xhtml)
        nav_texts = [entry["text"] for entry in result.nav_entries]
        self.assertIn("Architektura", nav_texts)
        self.assertIn("Architektura - Co to jest", nav_texts)
        self.assertIn("Architektura - Jak działa", nav_texts)
        self.assertIn("Zależności systemowe", nav_texts)

    def test_navigation_builders_keep_level_three_sections(self):
        toc_entries = [
            {"file_name": "chapter_001.xhtml", "id": "chapter-1", "text": "Chapter 1", "level": 1},
            {"file_name": "chapter_001.xhtml", "id": "section-a", "text": "Section A", "level": 2},
            {"file_name": "chapter_001.xhtml", "id": "section-a-1", "text": "Section A.1", "level": 3},
        ]

        nav_xhtml = _build_nav_xhtml(toc_entries=toc_entries, title="TOC", language="en")
        toc_ncx = _build_toc_ncx(toc_entries=toc_entries, title="TOC", package_identifier="pkg-id")

        self.assertGreaterEqual(nav_xhtml.count("<ol>"), 3)
        self.assertIn('href="chapter_001.xhtml#section-a-1"', nav_xhtml)
        self.assertIn('content="3"', toc_ncx)
        self.assertIn('src="chapter_001.xhtml#section-a-1"', toc_ncx)

    def test_toc_ncx_reuses_play_order_for_duplicate_targets(self):
        toc_entries = [
            {"file_name": "chapter_001.xhtml", "id": "feature", "text": "Feature", "level": 1},
            {"file_name": "chapter_001.xhtml", "id": "feature", "text": "Feature Intro", "level": 2},
            {"file_name": "chapter_002.xhtml", "id": "closing", "text": "Closing", "level": 1},
        ]

        toc_ncx = _build_toc_ncx(toc_entries=toc_entries, title="TOC", package_identifier="pkg-id")

        duplicate_orders = re.findall(r'playOrder="(\d+)">\s*<navLabel><text>(?:Feature|Feature Intro)</text>', toc_ncx)
        self.assertEqual(duplicate_orders, ["1", "1"])
        self.assertIn('playOrder="2"', toc_ncx)

    def test_should_include_in_toc_rejects_synthetic_and_truncated_headings(self):
        self.assertFalse(_should_include_in_toc("chapter_001", 1))
        self.assertFalse(_should_include_in_toc("Planning and", 2))
        self.assertFalse(_should_include_in_toc("• Purpose", 2))
        self.assertTrue(_should_include_in_toc("Chapter 1: Introduction", 1))
        self.assertTrue(_should_include_in_toc("Solution Architecture", 2))

    def test_problem_solution_links_prefer_explicit_caption_numbers_over_sequential_fallback(self):
        xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <section id="section-chapter_012">
      <h1>Easy Exercises</h1>
      <figure class="chess-problem"><img class="chess-diagram" src="images/chess_001.png" alt=""/></figure>
      <figure class="chess-problem">
        <figcaption class="diagram-caption"><span class="exercise-number">31.</span> Capablanca – Piazzini, Buenos Aires 1911</figcaption>
        <img class="chess-diagram" src="images/chess_002.png" alt=""/>
      </figure>
    </section>
  </body>
</html>
"""
        updated = _inject_problem_solution_links(
            xhtml,
            chapter_name="chapter_012.xhtml",
            solution_targets={"31": "chapter_015.xhtml#solution-31"},
            ordered_problem_refs=[
                {
                    "problem_file": "chapter_012.xhtml",
                    "exercise_num": "31",
                    "solution_href": "chapter_015.xhtml#solution-31",
                }
            ],
        )

        tree = etree.fromstring(updated.encode("utf-8"))
        ns = {"x": "http://www.w3.org/1999/xhtml"}
        figures = tree.findall(".//x:figure", namespaces=ns)

        self.assertEqual(figures[0].get("id"), None)
        self.assertIsNone(figures[0].find('.//x:p[@class="problem-solution-link"]', namespaces=ns))
        self.assertEqual(figures[1].get("id"), "exercise-31")
        self.assertIsNotNone(figures[1].find('.//x:p[@class="problem-solution-link"]', namespaces=ns))

    def test_repair_exercise_chapter_backfills_missing_game_caption_from_solution_title(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Easy Exercises</title></head>
  <body>
    <section id="section-chapter_012">
      <h1>Easy Exercises</h1>
      <figure class="chess-problem" id="exercise-31">
        <img class="chess-diagram" src="images/chess_002.png" alt=""/>
      </figure>
    </section>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_012.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            targets = _repair_exercise_chapter(
                chapter_path,
                solution_targets={"31": "chapter_015.xhtml#solution-31"},
                solution_titles={"31": "31. Jose Raul Capablanca – Luis Piazzini, Buenos Aires 1911"},
            )

            updated = chapter_path.read_text(encoding="utf-8")

        self.assertEqual(targets["31"], "chapter_012.xhtml#exercise-31")
        self.assertIn('class="diagram-caption"', updated)
        self.assertIn("Capablanca", updated)
        self.assertIn("Piazzini", updated)
        self.assertIn("Go to solution 31", updated)

    def test_process_chapter_rebuilds_reference_section_as_clickable_list(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1>References</h1>
    <p>[1] Semantic EPUB Guide https://example.com/guide; [2] Polish Layout Notes doi:10.1000/xyz</p>
    <p>SRC-01 EPUB 3.3 Specification www.w3.org/TR/epub-33/</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_references.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertIn("<ol>", result.xhtml)
        self.assertGreaterEqual(result.xhtml.count('class="reference-entry"'), 3)
        self.assertIn('class="reference-id"><strong>[1]</strong></span>', result.xhtml)
        self.assertIn('href="https://example.com/guide"', result.xhtml)
        self.assertIn('href="https://doi.org/10.1000/xyz"', result.xhtml)
        self.assertIn('href="https://www.w3.org/TR/epub-33/"', result.xhtml)

    def test_process_chapter_repairs_split_reference_links_and_keeps_clickable_entries(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1>References</h1>
    <p>[1] Visa Waiver Program <a href="https://usa.visa">https://usa.visa</a>.gov/travel/business/visa-waiver-program.html</p>
    <p>SRC-02 Annual Report https://www.example.com/reports<br/>/2026/q1</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_references_split.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertGreaterEqual(result.xhtml.count('class="reference-entry"'), 2)
        self.assertIn('href="https://usa.visa.gov/travel/business/visa-waiver-program.html"', result.xhtml)
        self.assertIn('href="https://www.example.com/reports/2026/q1"', result.xhtml)

    def test_rebuild_toc_entries_from_final_chapters_uses_final_anchor_ids(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Dense Handbook</title></head>
  <body>
    <section>
      <h1 id="dense-handbook">Dense Handbook</h1>
      <p>Intro.</p>
      <h2 id="solution-architecture">Solution Architecture</h2>
      <p>Body.</p>
    </section>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_001.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            entries = _rebuild_toc_entries_from_final_chapters(
                [chapter_path],
                fallback_entries=[
                    {
                        "file_name": "chapter_001.xhtml",
                        "id": "stale-anchor",
                        "text": "Solution Architecture",
                        "level": 2,
                    }
                ],
            )

        self.assertEqual(entries[0]["id"], "dense-handbook")
        self.assertTrue(any(entry["id"] == "solution-architecture" for entry in entries))
        self.assertFalse(any(entry["id"] == "stale-anchor" for entry in entries))

    def test_process_chapter_rebuilds_polish_references_from_anchor_labels_and_list_items(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Źródła</title></head>
  <body>
    <h1>Źródła</h1>
    <p>[1] Visa Waiver Program - Official travel guidance <a href="https://usa.visa.gov/travel/business/visa-waiver-program.html">official source</a></p>
    <ul>
      <li>SRC-02 Interchange Caps: Debit cards <a href="https://example.com/debit">details</a></li>
    </ul>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_zrodla.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Źródła",
                author="Tester",
                language="pl",
            )

        self.assertGreaterEqual(result.xhtml.count('class="reference-entry"'), 2)
        self.assertIn('class="reference-description">Official travel guidance</span>', result.xhtml)
        self.assertIn('href="https://usa.visa.gov/travel/business/visa-waiver-program.html"', result.xhtml)
        self.assertIn('href="https://example.com/debit"', result.xhtml)

    def test_process_chapter_splits_glued_reference_urls_into_clickable_links(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1>References</h1>
    <p>[3] Multi-source entry - Combined endpoints https://example.com/ahttps://example.com/b</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_glued_refs.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertIn('href="https://example.com/a"', result.xhtml)
        self.assertIn('href="https://example.com/b"', result.xhtml)
        self.assertNotIn('href="https://example.com/ahttps://example.com/b"', result.xhtml)

    def test_process_chapter_rebuilds_reference_subsection_inside_regular_chapter(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport kwartalny</title></head>
  <body>
    <h1>Raport kwartalny</h1>
    <p>Wprowadzenie do raportu opisuje kontekst biznesowy i cele publikacji.</p>
    <h2>Źródła</h2>
    <p>[1] Visa Waiver Program - Official guidance <a href="https://usa.visa">https://usa.visa</a><br/>.gov/travel/business/visa-waiver-program.html</p>
    <p>SRC-02 Tabela limitów https://example.com/limits%2</p>
    <h2>Wnioski</h2>
    <p>Końcowe wnioski pozostają zwykłą treścią rozdziału.</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_ref_subsection.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Raport kwartalny",
                author="Tester",
                language="pl",
            )

        self.assertIn(">Źródła</h2>", result.xhtml)
        self.assertGreaterEqual(result.xhtml.count('class="reference-entry"'), 2)
        self.assertIn('href="https://usa.visa.gov/travel/business/visa-waiver-program.html"', result.xhtml)
        self.assertIn('href="https://example.com/limits"', result.xhtml)
        self.assertIn(">Wnioski</h2>", result.xhtml)
        self.assertIn("Końcowe wnioski pozostają zwykłą treścią rozdziału.", result.xhtml)

    def test_process_chapter_keeps_ambiguous_reference_fragment_as_review_text(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1>References</h1>
    <p>[7] Broken registry notice https://the</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_broken_refs.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertIn('class="reference-entry"', result.xhtml)
        self.assertIn("Broken registry notice", result.xhtml)
        self.assertIn("Link requires manual review.", result.xhtml)
        self.assertNotIn("Unresolved URL:", result.xhtml)
        self.assertNotIn("https://the", result.xhtml)
        self.assertNotIn('href="https://the"', result.xhtml)

    def test_process_chapter_reuses_existing_reference_entry_markup_without_escaping_or_duplicate_ids(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1 id="references">References</h1>
    <ul>
      <li class="reference-entry">
        <p class="reference-label"><span class="reference-id"><strong>[R1]</strong></span> <span class="reference-title">EMVCo</span> - <span class="reference-description">3-D Secure overview</span></p>
        <p class="reference-links"><a class="reference-link" href="https://www.emvco.com/emvtechnologies/3-d-secure/">https://www.emvco.com/emvtechnologies/3-d-secure/</a></p>
      </li>
    </ul>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_existing_reference_entries.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertIn('class="reference-entry"', result.xhtml)
        self.assertIn('href="https://www.emvco.com/emvtechnologies/3-d-secure/"', result.xhtml)
        self.assertNotIn("&lt;p class=&quot;reference-label&quot;&gt;", result.xhtml)
        self.assertEqual(result.xhtml.count('id="references"'), 1)

    def test_process_chapter_pairs_reference_descriptors_with_following_url_register(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1>References</h1>
    <p>[R2] Mastercard - EU regulations / opis czterostronnego modelu.</p>
    <p>[R3] Mastercard Chargebacks Made Simple Guide - lifecycle of a transaction.</p>
    <p>https://www.mastercard.com/mt/en/forthe-world/about-us/eu-regulations.html https://www.mastercard.us/content/dam/public/mastercardcom/na/global-site/documents/chargebacks-made-simpleguide.pdf</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_descriptor_links.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertGreaterEqual(result.xhtml.count('class="reference-entry"'), 2)
        self.assertIn("Mastercard - EU regulations", result.xhtml)
        self.assertIn("Mastercard Chargebacks Made Simple Guide", result.xhtml)
        self.assertIn('href="https://www.mastercard.com/mt/en/forthe-world/about-us/eu-regulations.html"', result.xhtml)
        self.assertIn('href="https://www.mastercard.us/content/dam/public/mastercardcom/na/global-site/documents/chargebacks-made-simpleguide.pdf"', result.xhtml)

    def test_process_chapter_splits_embedded_protocol_reference_urls_and_repairs_tail(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>References</title></head>
  <body>
    <h1>References</h1>
    <p>[R9] PCI DSS and EBA RTS <a href="https://www.pcisecuritystandards.org/standards/pci-dss/https://www.eba.europa.eu/sites/default/files/document_library/Publications/Draft%20">https://www.pcisecuritystandards.org/standards/pci-dss/https://www.eba.europa.eu/sites/default/files/document_library/Publications/Draft%20</a>Technical%20Standards/2022/EBA-RTS-2022-03.pdf</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_embedded_protocol_refs.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="References",
                author="Tester",
                language="en",
            )

        self.assertIn('href="https://www.pcisecuritystandards.org/standards/pci-dss/"', result.xhtml)
        self.assertIn('href="https://www.eba.europa.eu/sites/default/files/document_library/Publications/Draft%20Technical%20Standards/2022/EBA-RTS-2022-03.pdf"', result.xhtml)
        self.assertNotIn('href="https://www.pcisecuritystandards.org/standards/pci-dss/https://www.eba.europa.eu', result.xhtml)

    def test_extract_reference_entries_from_table_rows_keeps_mixed_reference_model(self):
        entries = _extract_reference_entries_from_block(
            {
                "type": "table",
                "headers": ["ID", "Title", "URL"],
                "rows": [
                    ["[1]", "Semantic EPUB Guide", "https://example.com/guide"],
                    ["SRC-02", "Polish Layout Notes - Official guidance", "https://example.com/layout"],
                ],
            }
        )

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["source_id"], "[1]")
        self.assertEqual(entries[0]["title"], "Semantic EPUB Guide")
        self.assertEqual(entries[0]["links"], ["https://example.com/guide"])
        self.assertEqual(entries[1]["source_id"], "SRC-02")
        self.assertEqual(entries[1]["description"], "Official guidance")

    def test_process_chapter_merges_split_decimal_fragments_without_building_ordered_list(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Fees</title></head>
  <body>
    <h1>Fees</h1>
    <p>0.</p>
    <p>2% dla debetowych</p>
    <p>0.</p>
    <p>3% dla kredytowych</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_fees.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Fees",
                author="Tester",
                language="pl",
            )

        self.assertIn("0.2% dla debetowych", result.xhtml)
        self.assertIn("0.3% dla kredytowych", result.xhtml)
        self.assertNotIn("<ol>", result.xhtml)

    def test_process_chapter_turns_private_use_bullets_into_real_list(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Checklist</title></head>
  <body>
    <h1>Checklist</h1>
    <p> Pierwszy punkt</p>
    <p> Drugi punkt</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_bullets.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Checklist",
                author="Tester",
                language="pl",
            )

        self.assertIn("<ul>", result.xhtml)
        self.assertIn("<li class=\"bullet-item\">Pierwszy punkt</li>", result.xhtml)
        self.assertIn("<li class=\"bullet-item\">Drugi punkt</li>", result.xhtml)

    def test_process_chapter_demotes_promotional_banner_and_attaches_caption_near_figure(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport</title></head>
  <body>
    <h1>Raport</h1>
    <h2>Material sponsorowany - R4</h2>
    <p>59. Friedmann - Alekhine, Czechoslovakia (simul) 1925</p>
    <figure><img src="images/diagram.png" alt=""/></figure>
    <figure><img src="images/diagram2.png" alt=""/></figure>
    <p>60. Euwe - Alekhine, Amsterdam 1930</p>
    <p>Treść po podpisie pozostaje osobnym akapitem.</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_captions.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Raport",
                author="Tester",
                language="pl",
            )

        self.assertIn('<p class="promo-banner">Material sponsorowany - R4</p>', result.xhtml)
        self.assertNotIn("<h2>Material sponsorowany - R4</h2>", result.xhtml)
        self.assertGreaterEqual(result.xhtml.count("<figcaption"), 2)
        self.assertIn("Friedmann - Alekhine", result.xhtml)
        self.assertIn("Euwe - Alekhine", result.xhtml)
        self.assertNotIn('class="solution-entry"', result.xhtml)
        self.assertIn("Treść po podpisie pozostaje osobnym akapitem.", result.xhtml)

    def test_process_chapter_keeps_richer_h3_navigation_outline(self):
        h3_sections = "\n".join(
            f"<h3>Sekcja {index}</h3><p>Opis sekcji {index} rozwija temat nawigacji i struktury dokumentu.</p>"
            for index in range(1, 9)
        )
        chapter_source = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Architektura publikacji</title></head>
  <body>
    <h1>Architektura publikacji</h1>
    <h2>Warstwa główna</h2>
    <p>Wprowadzenie do warstwy głównej porządkuje temat przed wejściem w szczegółowe sekcje.</p>
    {h3_sections}
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_outline.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Architektura publikacji",
                author="Tester",
                language="pl",
            )

        level_three_entries = [entry["text"] for entry in result.nav_entries if entry["level"] == 3]
        self.assertEqual(len(level_three_entries), 8)
        self.assertIn("Sekcja 8", level_three_entries)

    def test_finalize_epub_for_kindle_repairs_package_metadata_and_spine(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid"></dc:identifier>
    <dc:title>0123456789abcdef0123456789abcdef</dc:title>
    <dc:language>pol</dc:language>
    <dc:creator id="creator">KindleMaster</dc:creator>
    <dc:description></dc:description>
  </metadata>
  <manifest>
    <item id="style" href="style/default.css" media-type="text/css"/>
    <item id="cover-image" href="images/cover.png" media-type="image/png"/>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="nav"/>
    <itemref idref="chapter_0"/>
  </spine>
</package>
"""
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Forest Walk</title>
  </head>
  <body>
    <section>
      <h1>Forest Walk</h1>
      <p class="author">Jane Doe</p>
      <p>This practical field guide explains how to read forest trails, observe landmarks, and move through unfamiliar terrain with calm, deliberate navigation decisions.</p>
    </section>
  </body>
</html>
"""
        cover_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Cover</title></head>
  <body><img src="images/cover.png" alt=""/></body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Start</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/cover.xhtml": cover_source,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
                "EPUB/images/cover.png": b"\xff\xd8\xff\xe0synthetic-jpeg-cover",
            }
        )

        cleaned_epub = finalize_epub_for_kindle(
            epub_bytes,
            title="0123456789abcdef0123456789abcdef",
            author="KindleMaster",
            language="pol",
        )

        with TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(io.BytesIO(cleaned_epub), "r") as archive:
                archive.extractall(temp_dir)

            opf_tree = etree.parse(str(Path(temp_dir) / "EPUB" / "content.opf"))
            root = opf_tree.getroot()
            ns = {
                "opf": "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }

            self.assertIn("dcterms:", root.get("prefix", ""))
            self.assertEqual(root.findtext(".//dc:title", namespaces=ns), "Forest Walk")
            self.assertEqual(root.findtext(".//dc:creator", namespaces=ns), "Jane Doe")
            self.assertEqual(root.findtext(".//dc:language", namespaces=ns), "en")
            self.assertRegex(root.findtext(".//dc:description", namespaces=ns), r"forest trails")
            self.assertRegex(root.findtext(".//dc:date", namespaces=ns), r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(
                root.findtext(".//opf:meta[@property='dcterms:modified']", namespaces=ns),
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )
            self.assertRegex(root.findtext(".//dc:identifier", namespaces=ns), r"^urn:uuid:[0-9a-f-]{36}$")

            manifest_items = {
                item.get("id"): item
                for item in root.findall(".//opf:manifest/opf:item", namespaces=ns)
            }
            self.assertEqual(manifest_items["cover-image"].get("media-type"), "image/jpeg")
            self.assertEqual(manifest_items["cover-image"].get("href"), "images/cover.jpeg")
            self.assertIn("cover-image", manifest_items["cover-image"].get("properties", ""))
            self.assertIn("nav", manifest_items["nav"].get("properties", ""))
            self.assertEqual(
                root.find(".//opf:metadata/opf:meta[@name='cover']", namespaces=ns).get("content"),
                "cover-image",
            )

            spine = root.findall(".//opf:spine/opf:itemref", namespaces=ns)
            self.assertEqual([item.get("idref") for item in spine], ["cover", "chapter_0", "nav"])
            self.assertEqual(spine[-1].get("linear"), "no")

            nav_xhtml = (Path(temp_dir) / "EPUB" / "nav.xhtml").read_text(encoding="utf-8")
            cover_xhtml = (Path(temp_dir) / "EPUB" / "cover.xhtml").read_text(encoding="utf-8")
            toc_ncx = (Path(temp_dir) / "EPUB" / "toc.ncx").read_text(encoding="utf-8")
            self.assertIn('epub:type="landmarks"', nav_xhtml)
            self.assertIn('href="cover.xhtml"', nav_xhtml)
            self.assertIn('href="chapter_001.xhtml#forest-walk"', nav_xhtml)
            self.assertIn('src="images/cover.jpeg"', cover_xhtml)
            self.assertIn('content="1"', toc_ncx)
            self.assertIn('src="chapter_001.xhtml#forest-walk"', toc_ncx)

    def test_finalize_epub_for_kindle_localizes_polish_metadata_navigation_and_diagrams(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid"></dc:identifier>
    <dc:title>0123456789abcdef0123456789abcdef</dc:title>
    <dc:language>en</dc:language>
    <dc:creator id="creator">Technical Converter</dc:creator>
    <dc:description></dc:description>
  </metadata>
  <manifest>
    <item id="style" href="style/default.css" media-type="text/css"/>
    <item id="diagram" href="images/diagram.png" media-type="image/png"/>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="nav"/>
  </spine>
</package>
"""
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
  <head>
    <title>Przewodnik wdrożeniowy</title>
  </head>
  <body>
    <section>
      <h1>Przewodnik wdrożeniowy</h1>
      <p class="author">Jan Kowalski</p>
      <p>Ten praktyczny przewodnik opisuje architekturę wdrożenia, sposób integracji usług oraz zależności systemowe potrzebne do przygotowania finalnego wydania EPUB.</p>
      <h2>Architektura</h2>
      <h3>Integracja API</h3>
      <p>Opis integracji API pokazuje, jak kolejne komponenty współpracują ze sobą w procesie publikacji.</p>
      <figure class="technical-figure"><img src="images/diagram.png" alt=""/><figcaption>Architektura systemu</figcaption></figure>
    </section>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Start</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        tiny_png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO8B9SMAAAAASUVORK5CYII=")

        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
                "EPUB/images/diagram.png": tiny_png,
            }
        )

        cleaned_epub = finalize_epub_for_kindle(
            epub_bytes,
            title="0123456789abcdef0123456789abcdef",
            author="Technical Converter",
            language="en",
        )

        with TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(io.BytesIO(cleaned_epub), "r") as archive:
                archive.extractall(temp_dir)

            opf_tree = etree.parse(str(Path(temp_dir) / "EPUB" / "content.opf"))
            root = opf_tree.getroot()
            ns = {
                "opf": "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }

            self.assertEqual(root.findtext(".//dc:title", namespaces=ns), "Przewodnik wdrożeniowy")
            self.assertEqual(root.findtext(".//dc:creator", namespaces=ns), "Jan Kowalski")
            self.assertEqual(root.findtext(".//dc:language", namespaces=ns), "pl")
            self.assertRegex(root.findtext(".//dc:description", namespaces=ns), r"architekturę wdrożenia")

            chapter_xhtml = (Path(temp_dir) / "EPUB" / "chapter_001.xhtml").read_text(encoding="utf-8")
            nav_xhtml = (Path(temp_dir) / "EPUB" / "nav.xhtml").read_text(encoding="utf-8")

            self.assertIn('lang="pl"', chapter_xhtml)
            self.assertIn('xml:lang="pl"', chapter_xhtml)
            self.assertIn("Spis treści", nav_xhtml)
            self.assertIn("Początek tekstu", nav_xhtml)
            self.assertIn("Integracja API", nav_xhtml)
            self.assertIn('technical-figure detail-diagram', chapter_xhtml)
            self.assertIn('alt="Architektura systemu"', chapter_xhtml)

    def test_finalize_epub_for_kindle_rebuilds_metadata_and_toc_from_real_heading_when_title_is_technical(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid">legacy-id</dc:identifier>
    <dc:title>Emvc</dc:title>
    <dc:language>en</dc:language>
    <dc:creator id="creator">python-docx</dc:creator>
    <dc:description></dc:description>
  </metadata>
  <manifest>
    <item id="style" href="style/default.css" media-type="text/css"/>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="nav"/>
  </spine>
</package>
"""
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Emvc</title></head>
  <body>
    <section>
      <h1>Raport interoperacyjności</h1>
      <p class="author">Anna Nowak</p>
      <p>Ten raport opisuje architekturę procesu, zależności systemowe oraz implikacje biznesowe związane z wdrożeniem finalnego EPUB.</p>
    </section>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Legacy label</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap><navPoint id="legacy" playOrder="1"><navLabel><text>Legacy label</text></navLabel><content src="chapter_001.xhtml"/></navPoint></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
            }
        )

        cleaned_epub = finalize_epub_for_kindle(
            epub_bytes,
            title="Emvc",
            author="python-docx",
            language="en",
        )

        with TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(io.BytesIO(cleaned_epub), "r") as archive:
                archive.extractall(temp_dir)

            opf_tree = etree.parse(str(Path(temp_dir) / "EPUB" / "content.opf"))
            ns = {
                "opf": "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }
            root = opf_tree.getroot()
            nav_xhtml = (Path(temp_dir) / "EPUB" / "nav.xhtml").read_text(encoding="utf-8")
            chapter_xhtml = (Path(temp_dir) / "EPUB" / "chapter_001.xhtml").read_text(encoding="utf-8")

            self.assertEqual(root.findtext(".//dc:title", namespaces=ns), "Raport interoperacyjności")
            self.assertEqual(root.findtext(".//dc:creator", namespaces=ns), "Anna Nowak")
            self.assertIn("Raport interoperacyjności", nav_xhtml)
            self.assertIn("<title>Raport interoperacyjności</title>", chapter_xhtml)


    def test_derive_package_metadata_does_not_inject_publication_specific_training_defaults(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter_001 = root / "chapter_001.xhtml"
            chapter_002 = root / "chapter_002.xhtml"
            chapter_001.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><section><p>Front cover</p></section></body></html>
""",
                encoding="utf-8",
            )
            chapter_002.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><section><h1>Easy Exercises</h1><p>Exercises and solutions.</p></section></body></html>
""",
                encoding="utf-8",
            )

            title, author, language = _derive_package_metadata(
                [chapter_001, chapter_002],
                title="Unknown",
                author="Unknown",
                language="en",
                allow_training_defaults=True,
            )

        self.assertEqual(title, "Easy Exercises")
        self.assertEqual(author, "Unknown")
        self.assertEqual(language, "en")

    def test_looks_like_training_book_requires_structure_not_specific_title(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chapter_001 = root / "chapter_001.xhtml"
            chapter_001.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><section><h1>Introduction</h1><p>General non-training content without exercise markers.</p></section></body></html>
""",
                encoding="utf-8",
            )

            looks_like_training = _looks_like_training_book([chapter_001], title="The Woodpecker Method")

        self.assertFalse(looks_like_training)

    def test_normalize_text_repairs_generic_registered_mark_mojibake(self):
        self.assertEqual(_normalize_text_light("ACME\u0139\u02dd Guide"), "ACME\u00ae Guide")


if __name__ == "__main__":
    unittest.main()
