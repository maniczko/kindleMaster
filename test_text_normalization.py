import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from text_normalization import (
    TextCleanupConfig,
    clean_epub_text_package,
    normalize_epub_text_package,
    normalize_text,
    ocr_text_to_html_parts,
)


class TextNormalizationTests(unittest.TestCase):
    def test_normalize_text_repairs_glued_words_and_spacing(self):
        normalized = normalize_text("Visit thewebsite today.This is spaced badly !")

        self.assertEqual(normalized, "Visit the website today. This is spaced badly!")

    def test_normalize_text_compacts_links_and_refs(self):
        normalized = normalize_text("See Ref . 12, [ 7 ] and https : //example . com /docs /intro plus doi:10. 1000 /xyz")

        self.assertIn("Ref. 12", normalized)
        self.assertIn("[7]", normalized)
        self.assertIn("https://example.com/docs/intro", normalized)
        self.assertIn("doi:10.1000/xyz", normalized)

    def test_ocr_text_to_html_parts_splits_merged_paragraphs(self):
        source = (
            "Pierwszy akapit jest wystarczajaco dlugi, aby wygladac jak normalna tresc i konczy sie kropka.\n"
            "Drugi akapit zaczyna nowa mysl z wielkiej litery i tez ma wystarczajaco duzo slow.\n\n"
            "Rozdzial 2\n"
            "Kolejny akapit po naglowku opisuje dalsza tresc dokumentu."
        )

        html_parts = ocr_text_to_html_parts(source)

        self.assertGreaterEqual(len(html_parts), 4)
        self.assertEqual(html_parts[0], "<p>Pierwszy akapit jest wystarczajaco dlugi, aby wygladac jak normalna tresc i konczy sie kropka.</p>")
        self.assertEqual(html_parts[1], "<p>Drugi akapit zaczyna nowa mysl z wielkiej litery i tez ma wystarczajaco duzo slow.</p>")
        self.assertEqual(html_parts[2], "<h2>Rozdzial 2</h2>")

    def test_ocr_text_to_html_parts_does_not_promote_sponsored_or_value_lines_to_headings(self):
        html_parts = ocr_text_to_html_parts("Material sponsorowany - R4\n0. 2% dla debetowych\nOpis tresci pozostaje akapitem.")

        self.assertTrue(all(not part.startswith("<h") for part in html_parts))
        self.assertEqual(len(html_parts), 1)
        self.assertIn("Material sponsorowany - R4", html_parts[0])
        self.assertIn("0.2% dla debetowych", html_parts[0])
        self.assertIn("Opis tresci pozostaje akapitem.", html_parts[0])

    def test_normalize_epub_text_package_updates_chapter_documents(self):
        epub_bytes = _build_test_epub(
            chapter_markup=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<p>Visit thewebsite at https : //example . com /docs /intro and see Ref . 4.</p>"
                "</body></html>"
            )
        )

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            normalized_epub = normalize_epub_text_package(epub_bytes)

        with zipfile.ZipFile(io.BytesIO(normalized_epub), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("the website", chapter)
        self.assertIn('<a href="https://example.com/docs/intro">https://example.com/docs/intro</a>', chapter)
        self.assertIn("Ref. 4", chapter)

    def test_normalize_epub_text_package_linkifies_www_and_doi(self):
        epub_bytes = _build_test_epub(
            chapter_markup=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                "<p>See www.example.com/docs and doi:10.1000/xyz for details.</p>"
                "</body></html>"
            )
        )

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            normalized_epub = normalize_epub_text_package(epub_bytes)

        with zipfile.ZipFile(io.BytesIO(normalized_epub), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn('<a href="https://www.example.com/docs">https://www.example.com/docs</a>', chapter)
        self.assertIn('<a href="https://doi.org/10.1000/xyz">https://doi.org/10.1000/xyz</a>', chapter)

    def test_normalize_epub_text_package_repairs_split_anchor_urls(self):
        epub_bytes = _build_test_epub(
            chapter_markup=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<p>Source: <a href="https://usa.visa">https://usa.visa</a>.gov/travel/business/visa-waiver-program.html</p>'
                "</body></html>"
            )
        )

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            normalized_epub = normalize_epub_text_package(epub_bytes)

        with zipfile.ZipFile(io.BytesIO(normalized_epub), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn(
            '<a href="https://usa.visa.gov/travel/business/visa-waiver-program.html">https://usa.visa.gov/travel/business/visa-waiver-program.html</a>',
            chapter,
        )
        self.assertNotIn("https://usa.visa</a>.gov", chapter)

    def test_normalize_epub_text_package_repairs_split_anchor_urls_across_wbr(self):
        epub_bytes = _build_test_epub(
            chapter_markup=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                '<p>Source: <a href="https://usa.visa">https://usa.visa</a><wbr/>.gov/travel/business/visa-waiver-program.html</p>'
                "</body></html>"
            )
        )

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            normalized_epub = normalize_epub_text_package(epub_bytes)

        with zipfile.ZipFile(io.BytesIO(normalized_epub), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn(
            '<a href="https://usa.visa.gov/travel/business/visa-waiver-program.html">https://usa.visa.gov/travel/business/visa-waiver-program.html</a>',
            chapter,
        )
        self.assertNotIn("<wbr/>", chapter)

    def test_clean_epub_text_package_emits_report_and_safe_autofixes(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>Wpraktyce issue ra funding ową generalledger 10 %.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with TemporaryDirectory() as temp_dir:
            dictionary_path = Path(temp_dir) / "domain.json"
            dictionary_path.write_text(
                json.dumps(
                    {
                        "terms": [
                            {
                                "canonical": "general ledger",
                                "variants": ["generalledger"],
                                "lang": "en",
                                "protected": True,
                            }
                        ],
                        "forced_merges": {
                            "issue ra": "issuera",
                            "funding ową": "fundingową",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch(
                "text_cleanup_engine.run_epubcheck",
                return_value={"status": "passed", "tool": "epubcheck", "messages": []},
            ):
                result = clean_epub_text_package(
                    epub_bytes,
                    config=TextCleanupConfig(
                        language_hint="pl",
                        domain_dictionary_path=str(dictionary_path),
                        emit_text_diff=True,
                    ),
                )

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("W praktyce", chapter)
        self.assertIn("issuera", chapter)
        self.assertIn("fundingową", chapter)
        self.assertIn("general ledger", chapter)
        self.assertIn("10%", chapter)
        self.assertGreaterEqual(result.summary["auto_fix_count"], 4)
        self.assertEqual(result.epubcheck["status"], "passed")
        self.assertIn("EPUB/chapter_001.xhtml", result.chapter_diffs)
        self.assertIn("# Text Cleanup Report", result.markdown_report)

    def test_clean_epub_text_package_marks_uncertain_merge_as_review_needed(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>kart owe pozostają do weryfikacji.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(
                epub_bytes,
                config=TextCleanupConfig(language_hint="pl", safe_threshold=0.9, review_threshold=0.6),
            )

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("kart owe", chapter)
        review_decisions = [(decision.before, decision.after, decision.status) for decision in result.decisions]
        self.assertIn(("kart owe", "kartowe", "review_needed"), review_decisions)

    def test_clean_epub_text_package_repairs_inline_decimal_percent_fragments(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>Stawka wynosi 0. 2% dla debetowych i 0. 3% dla kredytowych.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl"))

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("0.2% dla debetowych", chapter)
        self.assertIn("0.3% dla kredytowych", chapter)

    def test_clean_epub_text_package_repairs_single_letter_split_domain_terms(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>Srodki sa rezerwowane u i ssuera, potem trafiaja w lancuch a cquiringowy, "
            "a ownerem wyjatku pozostaje o wnerem procesu. W alidowac trzeba tez timeouty.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl"))

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("u issuera", chapter)
        self.assertIn("lancuch acquiringowy", chapter)
        self.assertIn("ownerem wyjatku pozostaje ownerem procesu", chapter)
        self.assertIn("Walidowac trzeba", chapter)

    def test_clean_epub_text_package_repairs_glued_stopword_prefix_word(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>Wogloszeniu opisano nowy model operacyjny.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl"))

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("W ogloszeniu", chapter)

    def test_clean_epub_text_package_skips_protected_tags(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>Wpraktyce w treści.</p>"
            "<pre>Wpraktyce w kodzie.</pre>"
            "<code>issue ra</code>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl"))

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("<p>W praktyce w treści.</p>", chapter)
        self.assertIn("<pre>Wpraktyce w kodzie.</pre>", chapter)
        self.assertIn("<code>issue ra</code>", chapter)

    def test_clean_epub_text_package_skips_heading_like_review_noise(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<h2>kart owe</h2>"
            "<p>Poprawny akapit pozostaje bez zmian.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl", long_document_mode=True))

        review_pairs = {(decision.before, decision.status) for decision in result.decisions}
        self.assertNotIn(("kart owe", "review_needed"), review_pairs)
        self.assertNotIn(("kart owe", "blocked"), review_pairs)

    def test_clean_epub_text_package_ignores_unknown_terms_in_headings_and_captions(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<h2>Swimlanes</h2>"
            "<figcaption>Figure 1. Flowcharting Overview</figcaption>"
            "<p>Opis zwyklego akapitu.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="en", long_document_mode=True))

        unknown_terms = {item["term"] for item in result.unknown_terms}
        self.assertNotIn("swimlanes", unknown_terms)
        self.assertNotIn("flowcharting", unknown_terms)

    def test_clean_epub_text_package_blocks_pre_paginated_epub(self):
        epub_bytes = _build_test_epub(
            chapter_markup=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Wpraktyce</p></body></html>'
            ),
            extra_opf_metadata='<meta property="rendition:layout">pre-paginated</meta>',
        )

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl"))

        self.assertEqual(result.epub_bytes, epub_bytes)
        self.assertTrue(result.summary["package_blocked"])
        self.assertEqual(result.decisions[0].status, "blocked")

    def test_clean_epub_text_package_dedupes_duplicate_ids(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<a href="#co-to-jest">Skocz do sekcji</a>'
            '<h2 id="co-to-jest">Co to jest</h2>'
            '<p>Opis pierwszej sekcji.</p>'
            '<h2 id="co-to-jest">Co to jest</h2>'
            '<p>Opis drugiej sekcji.</p>'
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="pl"))

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertEqual(chapter.count('id="co-to-jest"'), 1)
        self.assertIn('id="co-to-jest-2"', chapter)
        self.assertIn('href="#co-to-jest"', chapter)

    def test_clean_epub_text_package_promotes_repeated_runtime_domain_terms(self):
        chapter_markup = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<h1>BABOK Guide</h1>"
            "<h2>CBAP i IIBA</h2>"
            "<p>BABOK wspiera CBAP i IIBA. BABOK porzadkuje praktyki analityczne.</p>"
            "<p>W treningu szachowym counterplay, kingside i queenside pojawiaja sie wielokrotnie. "
            "Counterplay i kingside wracaja takze w kolejnych przykladach, a queenside pozostaje terminem technicznym.</p>"
            "</body></html>"
        )
        epub_bytes = _build_test_epub(chapter_markup=chapter_markup)

        with patch(
            "text_cleanup_engine.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = clean_epub_text_package(epub_bytes, config=TextCleanupConfig(language_hint="en"))

        unknown_terms = {row["term"] for row in result.unknown_terms}
        self.assertNotIn("babok", unknown_terms)
        self.assertNotIn("cbap", unknown_terms)
        self.assertNotIn("iiba", unknown_terms)
        self.assertNotIn("counterplay", unknown_terms)
        self.assertNotIn("kingside", unknown_terms)
        self.assertNotIn("queenside", unknown_terms)


def _build_test_epub(*, chapter_markup: str, extra_opf_metadata: str = "") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype_info, "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "EPUB/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">test-book</dc:identifier>
    <dc:title>Test</dc:title>
    """
            + extra_opf_metadata
            + """
  </metadata>
  <manifest>
    <item id="chapter1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>
""",
        )
        archive.writestr("EPUB/chapter_001.xhtml", chapter_markup)
    return output.getvalue()


if __name__ == "__main__":
    unittest.main()
