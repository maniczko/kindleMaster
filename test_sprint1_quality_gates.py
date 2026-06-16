from __future__ import annotations

import io
import unittest
import zipfile

from epub_premium_scoring import score_epub_premium_quality
from quality_cockpit_issues import build_quality_cockpit_issue_groups


def _build_epub(
    *,
    chapters: dict[str, str],
    nav_entries: list[tuple[str, str]],
    title: str = "Sprint 1 QA Fixture",
    creator: str = "Quality Team",
    language: str = "pl",
) -> bytes:
    buffer = io.BytesIO()
    manifest_items = []
    spine_items = []
    files: dict[str, str | bytes] = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""",
        "EPUB/style/default.css": "body { font-family: serif; }",
        "EPUB/images/ad.jpg": b"fake-image",
    }
    for index, (name, body) in enumerate(chapters.items(), start=1):
        item_id = f"chapter_{index}"
        manifest_items.append(f'<item id="{item_id}" href="{name}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{item_id}"/>')
        files[f"EPUB/{name}"] = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<html xmlns='http://www.w3.org/1999/xhtml'>"
            f"<head><title>{title}</title></head><body>{body}</body></html>"
        )

    nav_links = "\n".join(f'<li><a href="{href}">{label}</a></li>' for label, href in nav_entries)
    files["EPUB/nav.xhtml"] = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<html xmlns='http://www.w3.org/1999/xhtml' xmlns:epub='http://www.idpf.org/2007/ops'>"
        f"<body><nav epub:type='toc'><ol>{nav_links}</ol></nav></body></html>"
    )
    files["EPUB/toc.ncx"] = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<ncx xmlns='http://www.daisy.org/z3986/2005/ncx/' version='2005-1'>"
        "<head><meta name='dtb:uid' content='sprint1'/></head>"
        f"<docTitle><text>{title}</text></docTitle><navMap/></ncx>"
    )
    files["EPUB/content.opf"] = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">sprint1</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{creator}</dc:creator>
    <dc:language>{language}</dc:language>
    <dc:publisher>KindleMaster QA</dc:publisher>
  </metadata>
  <manifest>
    {"".join(manifest_items)}
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
    <item id="ad-image" href="images/ad.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine toc="ncx">{"".join(spine_items)}</spine>
</package>
"""

    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            compression = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(name, data, compress_type=compression)
    return buffer.getvalue()


class Sprint1QualityGateTests(unittest.TestCase):
    def test_quality_score_blocks_ads_and_ocr_artifacts_in_reader_spine(self) -> None:
        editorial_text = " ".join(
            [
                "Ten rozdzial opisuje proces wydawniczy i jakosc czytania na czytniku Kindle."
                for _ in range(40)
            ]
        )
        epub_bytes = _build_epub(
            chapters={
                "chapter_001.xhtml": (
                    "<h1>Raport jakosci</h1>"
                    f"<p>{editorial_text}</p>"
                    "<p>Pro- jekt oraz Ä Ä Ä widoczne artefakty OCR nie moga trafic do wydania premium.</p>"
                ),
                "chapter_002.xhtml": "<h1>Reklama</h1><p>Reklama</p><img src='images/ad.jpg' alt=''/>",
                "chapter_003.xhtml": "<h1>Material sponsorowany</h1><p>Material sponsorowany</p>",
            },
            nav_entries=[
                ("Raport jakosci", "chapter_001.xhtml"),
                ("Reklama", "chapter_002.xhtml"),
                ("Material sponsorowany", "chapter_003.xhtml"),
            ],
        )

        payload = score_epub_premium_quality(epub_bytes, epubcheck={"status": "passed", "messages": []})
        blockers = {
            issue["code"]
            for issue in payload["issues"]
            if issue["severity"] == "blocker"
        }

        self.assertEqual(payload["release_verdict"], "release_blocked")
        self.assertFalse(payload["kindle_ready"])
        self.assertFalse(payload["premium_ready"])
        self.assertIn("magazine_non_content_chapter", blockers)
        self.assertIn("ocr_suspicious_unicode", blockers)
        self.assertIn("kindle_ready_blocked_by_quality", blockers)
        self.assertGreater(payload["metrics"]["non_content_chapter_count"], 0)
        self.assertGreater(payload["metrics"]["text_artifacts"]["counts"]["ocr_junk_count"], 0)

    def test_quality_score_flags_bad_toc_as_release_risk(self) -> None:
        epub_bytes = _build_epub(
            chapters={
                "chapter_001.xhtml": "<h1>Rozdzial</h1><p>Pelny tekst rozdzialu do kontroli TOC.</p>",
            },
            nav_entries=[
                ("Spis tresci", "chapter_001.xhtml"),
                ("Galeria", "chapter_001.xhtml"),
                ("Object 12", "chapter_001.xhtml"),
                (
                    "To jest bardzo dlugi lead artykulu zamiast krotkiego tytulu nawigacji i powinien trafic do kontroli jakosci",
                    "chapter_001.xhtml",
                ),
            ],
        )

        payload = score_epub_premium_quality(epub_bytes, epubcheck={"status": "passed", "messages": []})
        issue_codes = [issue["code"] for issue in payload["issues"]]

        self.assertLess(payload["scores"]["toc_quality_score"], 4.0)
        self.assertEqual(payload["metrics"]["toc_noise_entry_count"], 3)
        self.assertIn("toc_non_content_entry", issue_codes)
        self.assertFalse(payload["premium_ready"])

    def test_quality_state_preserves_premium_score_blockers_for_bad_toc_and_ai_notes(self) -> None:
        issue_groups = build_quality_cockpit_issue_groups(
            premium_scoring={
                "status": "failed",
                "kindle_ready": False,
                "premium_ready": False,
                "premium_score": 4.8,
                "issues": [
                    {
                        "severity": "blocker",
                        "code": "bad_toc_quality_score",
                        "message": "TOC score is below the Sprint 1 release threshold.",
                        "source": "premium_scoring",
                        "suggested_action": "Rebuild navigation from recovered headings before release.",
                    },
                    {
                        "severity": "blocker",
                        "code": "ai_notes_leaked_into_epub",
                        "message": "AI notes or assistant comments are visible in reader text.",
                        "source": "premium_scoring",
                        "suggested_action": "Strip generated notes from the reading spine and regenerate the score.",
                    },
                ],
            }
        )
        blocker_codes = [item["code"] for item in issue_groups["blockers"]]

        self.assertIn("bad_toc_quality_score", blocker_codes)
        self.assertIn("ai_notes_leaked_into_epub", blocker_codes)
        self.assertIn("kindle_ready_blocked_by_quality", blocker_codes)
        self.assertEqual(issue_groups["warnings"], [])


if __name__ == "__main__":
    unittest.main()
