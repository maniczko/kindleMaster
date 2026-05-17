from __future__ import annotations

import io
import unittest
import zipfile

from epub_premium_scoring import (
    apply_magazine_premium_quality_to_scoring,
    build_magazine_premium_quality_contract,
    refresh_magazine_article_map_from_epub,
    score_epub_premium_quality,
)


def _minimal_epub() -> bytes:
    return _minimal_epub_with_nav(["Main Feature"])


def _minimal_epub_with_nav(nav_labels: list[str]) -> bytes:
    buffer = io.BytesIO()
    nav_items = "\n".join(
        f'<li><a href="chapter_001.xhtml#{index}">{label}</a></li>'
        for index, label in enumerate(nav_labels, start=1)
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""",
        )
        archive.writestr(
            "EPUB/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">premium-scoring-fixture</dc:identifier>
    <dc:title>Magazine Issue</dc:title>
    <dc:creator>Editorial Team</dc:creator>
    <dc:language>en</dc:language>
    <dc:publisher>Publisher</dc:publisher>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter1"/></spine>
</package>
""",
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body><nav epub:type="toc"><ol>{nav_items}</ol></nav></body>
</html>
""",
        )
        archive.writestr(
            "EPUB/chapter_001.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Main Feature</title></head>
  <body><h1>Main Feature</h1><p>This is a clean article paragraph for scoring.</p></body>
</html>
""",
        )
    return buffer.getvalue()


class EpubPremiumScoringTests(unittest.TestCase):
    def test_epubcheck_non_linear_unreachable_caps_score_and_blocks_release(self) -> None:
        scoring = score_epub_premium_quality(
            _minimal_epub(),
            epubcheck={
                "status": "failed",
                "messages": [
                    "ERROR(OPF-096): Non-linear content is not reachable from the spine or navigation.",
                ],
            },
        )

        codes = [issue["code"] for issue in scoring["issues"]]

        self.assertFalse(scoring["technical_valid"])
        self.assertFalse(scoring["kindle_ready"])
        self.assertEqual(scoring["release_verdict"], "release_blocked")
        self.assertLessEqual(scoring["premium_score"], 4.5)
        self.assertIn("epubcheck_non_linear_unreachable", codes)

    def test_magazine_contract_blocks_premium_when_artifacts_exceed_threshold(self) -> None:
        base_scoring = {
            "technical_valid": True,
            "kindle_ready": True,
            "premium_ready": True,
            "status": "passed",
            "release_verdict": "release_ready",
            "premium_score": 9.3,
            "issues": [],
            "issue_counts": {},
            "scores": {"premium_score": 9.3},
        }
        contract = build_magazine_premium_quality_contract(
            premium_scoring=base_scoring,
            validation_status="passed",
            magazine_audit={
                "article_map": {
                    "editorial_article_count": 5,
                    "toc_coverage": 1.0,
                    "front_matter_before_articles": True,
                }
            },
            text_artifacts={"artifact_rate_per_1000_words": 0.75},
        )
        merged = apply_magazine_premium_quality_to_scoring(base_scoring, contract)

        self.assertEqual(contract["status"], "failed")
        self.assertFalse(merged["premium_ready"])
        self.assertEqual(merged["release_verdict"], "release_blocked")
        self.assertIn("magazine_text_artifact_rate_high", [issue["code"] for issue in merged["issues"]])

    def test_magazine_pass_with_review_cannot_remain_premium_ready(self) -> None:
        base_scoring = {
            "technical_valid": True,
            "kindle_ready": True,
            "premium_ready": True,
            "status": "passed",
            "release_verdict": "release_ready",
            "premium_score": 9.3,
            "issues": [],
            "issue_counts": {},
            "scores": {"premium_score": 9.3},
        }
        contract = build_magazine_premium_quality_contract(
            premium_scoring=base_scoring,
            validation_status="passed",
            magazine_audit={
                "article_map": {
                    "editorial_article_count": 5,
                    "toc_coverage": 1.0,
                    "front_matter_before_articles": True,
                    "low_resolution_image_count": 2,
                }
            },
            text_artifacts={"artifact_rate_per_1000_words": 0.0},
        )

        merged = apply_magazine_premium_quality_to_scoring(base_scoring, contract)

        self.assertEqual(contract["status"], "passed_with_warnings")
        self.assertFalse(contract["premium_ready"])
        self.assertFalse(merged["premium_ready"])
        self.assertEqual(merged["release_verdict"], "ready_with_review")

    def test_magazine_article_map_uses_final_epub_nav_for_coverage(self) -> None:
        article_map = {
            "status": "failed",
            "article_count": 4,
            "editorial_article_count": 4,
            "toc_entry_count": 1,
            "toc_covered_article_count": 1,
            "toc_coverage": 0.25,
            "blockers": ["magazine_article_toc_coverage_below_95"],
            "review": [],
            "articles": [
                {"title": "Main Feature", "kind": "article", "toc_matched": True, "toc_excluded": False},
                {"title": "Second Feature", "kind": "article", "toc_matched": False, "toc_excluded": False},
                {"title": "Third Interview", "kind": "interview", "toc_matched": False, "toc_excluded": False},
                {"title": "Fourth Analysis", "kind": "article", "toc_matched": False, "toc_excluded": False},
            ],
        }
        epub_bytes = _minimal_epub_with_nav(
            ["Main Feature", "Second Feature", "Third Interview", "Fourth Analysis"]
        )

        refreshed = refresh_magazine_article_map_from_epub(article_map, epub_bytes)

        self.assertEqual(refreshed["coverage_source"], "final_epub_nav")
        self.assertEqual(refreshed["toc_coverage"], 1.0)
        self.assertEqual(refreshed["toc_missing_articles"], [])
        self.assertNotIn("magazine_article_toc_coverage_below_95", refreshed["blockers"])


if __name__ == "__main__":
    unittest.main()
