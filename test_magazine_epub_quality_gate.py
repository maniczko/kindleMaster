from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path

from scripts.verify_magazine_epub_quality import EpubSource, verify_epub
from epub_premium_scoring import (
    apply_magazine_premium_quality_to_scoring,
    build_magazine_premium_quality_contract,
)


def _chapter(title: str, *, words: int = 120) -> str:
    body = " ".join(["treść"] * words)
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<html xmlns='http://www.w3.org/1999/xhtml'><head>"
        f"<title>{title}</title></head><body>"
        f"<h1 id='start'>{title}</h1><p>{body}</p>"
        "</body></html>"
    )


def _build_magazine_epub(*, nav_links: list[tuple[str, str]], extra_spine: str = "") -> bytes:
    manifest_chapters = "\n".join(
        f'<item id="chapter{i}" href="chapter_{i:03d}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(1, 6)
    )
    spine_chapters = "\n".join(f'<itemref idref="chapter{i}"/>' for i in range(1, 6))
    nav_items = "\n".join(f'<li><a href="{href}">{label}</a></li>' for label, href in nav_links)
    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Strefa Test</dc:title>
    <dc:creator>Project Management Institute Poland Chapter</dc:creator>
    <dc:language>pl</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    {manifest_chapters}
    <item id="gallery" href="gallery.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    {spine_chapters}
    {extra_spine}
  </spine>
</package>
"""
    nav_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Spis treści</title></head>
  <body><nav epub:type="toc"><ol>{nav_items}</ol></nav></body>
</html>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""")
        archive.writestr("EPUB/content.opf", content_opf)
        archive.writestr("EPUB/nav.xhtml", nav_xhtml)
        for i in range(1, 6):
            archive.writestr(f"EPUB/chapter_{i:03d}.xhtml", _chapter(f"Artykuł {i}"))
        archive.writestr("EPUB/gallery.xhtml", _chapter("Galeria", words=8))
    return buffer.getvalue()


def _verify(epub_bytes: bytes):
    args = Namespace(min_toc_links=5, min_issue_toc_coverage=0.75, max_non_editorial_ratio=0.35)
    with tempfile.TemporaryDirectory() as temp_dir:
        epub_path = Path(temp_dir) / "magazine.epub"
        epub_path.write_bytes(epub_bytes)
        source = EpubSource(epub_path)
        try:
            return verify_epub(source, args)
        finally:
            source.close()


class MagazineEpubQualityGateTests(unittest.TestCase):
    def test_unreachable_non_linear_spine_content_fails_magazine_gate(self) -> None:
        epub_bytes = _build_magazine_epub(
            nav_links=[(f"Artykuł {i}", f"chapter_{i:03d}.xhtml#start") for i in range(1, 6)],
            extra_spine='<itemref idref="gallery" linear="no"/>',
        )

        report = _verify(epub_bytes)

        self.assertEqual(report.verdict, "FAIL")
        self.assertEqual(report.unreachable_non_linear_chapters, ["EPUB/gallery.xhtml"])

    def test_low_nav_coverage_of_linear_editorial_chapters_fails_magazine_gate(self) -> None:
        epub_bytes = _build_magazine_epub(
            nav_links=[(f"Artykuł {i}", "chapter_001.xhtml#start") for i in range(1, 6)]
        )

        report = _verify(epub_bytes)

        self.assertEqual(report.verdict, "FAIL")
        self.assertLess(report.nav_linear_editorial_coverage, 0.75)

    def test_magazine_premium_contract_blocks_low_article_toc_coverage(self) -> None:
        scoring = {
            "technical_valid": True,
            "kindle_ready": True,
            "premium_ready": True,
            "status": "passed",
            "release_verdict": "release_ready",
            "premium_score": 9.4,
            "issues": [],
            "issue_counts": {},
            "scores": {"premium_score": 9.4},
        }
        contract = build_magazine_premium_quality_contract(
            premium_scoring=scoring,
            validation_status="passed",
            magazine_audit={
                "article_map": {
                    "editorial_article_count": 8,
                    "toc_coverage": 0.75,
                    "front_matter_before_articles": True,
                    "articles": [],
                }
            },
            text_artifacts={"artifact_rate_per_1000_words": 0.1},
        )

        merged = apply_magazine_premium_quality_to_scoring(scoring, contract)
        codes = [issue["code"] for issue in merged["issues"]]

        self.assertEqual(contract["status"], "failed")
        self.assertIn("magazine_article_coverage_low", codes)
        self.assertFalse(merged["kindle_ready"])
        self.assertFalse(merged["premium_ready"])
        self.assertEqual(merged["release_verdict"], "release_blocked")

    def test_magazine_premium_contract_keeps_clean_magazine_ready(self) -> None:
        scoring = {
            "technical_valid": True,
            "kindle_ready": True,
            "premium_ready": True,
            "status": "passed",
            "release_verdict": "release_ready",
            "premium_score": 9.2,
            "issues": [],
            "issue_counts": {},
            "scores": {"premium_score": 9.2},
        }
        contract = build_magazine_premium_quality_contract(
            premium_scoring=scoring,
            validation_status="passed",
            magazine_audit={
                "article_map": {
                    "editorial_article_count": 6,
                    "toc_coverage": 1.0,
                    "front_matter_before_articles": True,
                    "non_content_chapter_count": 0,
                    "truncated_title_count": 0,
                    "high_risk_article_count": 0,
                    "low_resolution_image_count": 0,
                    "articles": [],
                }
            },
            text_artifacts={"artifact_rate_per_1000_words": 0.0},
        )

        merged = apply_magazine_premium_quality_to_scoring(scoring, contract)

        self.assertEqual(contract["status"], "passed")
        self.assertTrue(contract["premium_ready"])
        self.assertTrue(merged["premium_ready"])

    def test_magazine_premium_contract_separates_url_fragments_from_reader_text_artifacts(self) -> None:
        scoring = {
            "technical_valid": True,
            "kindle_ready": True,
            "premium_ready": False,
            "status": "passed",
            "release_verdict": "ready_with_review",
            "premium_score": 0.0,
            "issues": [],
            "issue_counts": {},
            "scores": {},
        }
        contract = build_magazine_premium_quality_contract(
            premium_scoring=scoring,
            validation_status="passed",
            magazine_audit={
                "article_map": {
                    "editorial_article_count": 8,
                    "toc_coverage": 1.0,
                    "front_matter_before_articles": True,
                    "articles": [],
                }
            },
            text_artifacts={
                "word_count": 26000,
                "artifact_count": 14,
                "artifact_rate_per_1000_words": 0.538,
                "counts": {
                    "split_word_count": 4,
                    "glued_word_count": 7,
                    "suspicious_url_fragment_count": 3,
                },
            },
        )
        codes = [issue["code"] for issue in contract["issues"]]

        self.assertEqual(contract["status"], "passed_with_warnings")
        self.assertNotIn("magazine_text_artifact_rate_high", codes)
        self.assertIn("magazine_url_fragment_review", codes)
        self.assertLess(contract["metrics"]["reader_text_artifact_rate_per_1000_words"], 0.5)


if __name__ == "__main__":
    unittest.main()
