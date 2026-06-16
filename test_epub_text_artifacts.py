import io
import unittest
import zipfile

from epub_text_artifacts import analyze_epub_text_artifacts
from quality_cockpit_issues import build_quality_cockpit_issue_groups


def _epub_with_documents(documents: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        for name, body in documents.items():
            archive.writestr(
                f"EPUB/{name}",
                (
                    "<?xml version='1.0' encoding='utf-8'?>"
                    "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
                    f"{body}"
                    "</body></html>"
                ),
            )
    return buffer.getvalue()


def _epub_with_linear_and_non_linear_documents() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "EPUB/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Linear Text Probe</dc:title>
    <dc:language>pl</dc:language>
    <dc:identifier id="bookid">linear-text-probe</dc:identifier>
  </metadata>
  <manifest>
    <item id="article" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="ad" href="chapter_002.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="article"/>
    <itemref idref="ad" linear="no"/>
    <itemref idref="nav" linear="no"/>
  </spine>
</package>""",
        )
        archive.writestr(
            "EPUB/chapter_001.xhtml",
            "<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<p>To jest czysty tekst artykulu z poprawnymi akapitami i bez widocznych artefaktow.</p>"
            "</body></html>",
        )
        archive.writestr(
            "EPUB/chapter_002.xhtml",
            "<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<h1>Reklama</h1><p>facebook. com/example linkedin. com/company/example Broken pro- ject text .</p>"
            "</body></html>",
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            "<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml'><body></body></html>",
        )
    return buffer.getvalue()


class EpubTextArtifactTests(unittest.TestCase):
    def test_clean_epub_text_has_low_artifact_rate(self) -> None:
        epub_bytes = _epub_with_documents(
            {
                "chapter_001.xhtml": "<p>This chapter has clean paragraphs and readable business analysis text.</p>",
            }
        )

        payload = analyze_epub_text_artifacts(epub_bytes)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["artifact_count"], 0)
        self.assertGreater(payload["word_count"], 0)

    def test_visible_artifacts_are_counted_per_document(self) -> None:
        epub_bytes = _epub_with_documents(
            {
                "chapter_001.xhtml": (
                    "<p>Broken pro- ject text and BusinessAnalysisPlanning without spacing ."
                    " See https : //example.com and Object 1.</p>"
                ),
            }
        )

        payload = analyze_epub_text_artifacts(epub_bytes)
        counts = payload["counts"]

        self.assertIn(payload["status"], {"passed_with_warnings", "failed"})
        self.assertGreater(counts["split_word_count"], 0)
        self.assertGreater(counts["glued_word_count"], 0)
        self.assertGreater(counts["punctuation_spacing_count"], 0)
        self.assertGreater(counts["suspicious_url_fragment_count"], 0)
        self.assertGreater(counts["technical_placeholder_count"], 0)
        self.assertEqual(payload["per_document"][0]["document_path"], "EPUB/chapter_001.xhtml")

    def test_non_linear_spine_documents_are_ignored(self) -> None:
        payload = analyze_epub_text_artifacts(_epub_with_linear_and_non_linear_documents())

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["artifact_count"], 0)
        self.assertEqual([item["document_path"] for item in payload["per_document"]], ["EPUB/chapter_001.xhtml"])

    def test_polish_and_mixed_language_lowercase_glued_words_are_counted(self) -> None:
        epub_bytes = _epub_with_documents(
            {
                "chapter_001.xhtml": (
                    "<p>Politykaiinfotainment oraz Wielkiefermywn wygladaja jak artefakty ekstrakcji. "
                    "Niezwyk&#322;amatkas&#322;ynnegopremiera i analizaryzyka tez powinny trafic do raportu.</p>"
                ),
            }
        )

        payload = analyze_epub_text_artifacts(epub_bytes)

        self.assertIn(payload["status"], {"passed_with_warnings", "failed"})
        self.assertGreaterEqual(payload["counts"]["glued_word_count"], 3)

    def test_known_clean_words_and_acronyms_are_not_counted_as_glued_noise(self) -> None:
        epub_bytes = _epub_with_documents(
            {
                "chapter_001.xhtml": (
                    "<p>Projektowe wymagania API oraz HTTP URL sa poprawnym tekstem technicznym. "
                    "Internationalization nie powinno byc karane jako sklejony token. "
                    "Romanowskiego, kontrasygnaty, cyberstalking, termomodernizacja i Dzieciofobia "
                    "oraz pozafinansowe, ponadprzeciętnej, polikryzysie i AccountAbility "
                    "sa rzadkimi, ale poprawnymi slowami w magazynie.</p>"
                ),
            }
        )

        payload = analyze_epub_text_artifacts(epub_bytes)

        self.assertEqual(payload["counts"]["glued_word_count"], 0)

    def test_valid_external_urls_are_not_counted_as_broken_fragments(self) -> None:
        epub_bytes = _epub_with_documents(
            {
                "chapter_001.xhtml": (
                    "<p>Kontakt: <a href='https://example.com/path'>https://example.com/path</a>. "
                    "Zepsuty zapis https : //example.com powinien nadal byc widoczny.</p>"
                ),
            }
        )

        payload = analyze_epub_text_artifacts(epub_bytes)

        self.assertEqual(payload["counts"]["suspicious_url_fragment_count"], 1)

    def test_cockpit_promotes_failed_artifact_rate(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            text_cleanup={
                "status": "passed",
                "artifact_rate": {
                    "status": "failed",
                    "artifact_count": 10,
                    "artifact_rate_per_1000_words": 7.5,
                },
            }
        )

        self.assertIn("text_artifact_rate_failed", [issue["code"] for issue in groups["blockers"]])


if __name__ == "__main__":
    unittest.main()
