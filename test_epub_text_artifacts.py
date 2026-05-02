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

