from __future__ import annotations

import io
import unittest
import zipfile

from ai_ocr_cleanup import AIOcrCleanupProviderResult
from ai_quality_intelligence import AIQualityProviders, evaluate_ai_quality_intelligence
from ai_toc_detection import AiTocCandidate, AiTocProviderResult


def _build_epub(*, chapters: dict[str, str], nav_entries: list[tuple[str, str]], title: str = "AI Fixture") -> bytes:
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
    files["EPUB/content.opf"] = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">ai-fixture</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    {"".join(manifest_items)}
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>{"".join(spine_items)}</spine>
</package>
"""
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            data = payload.encode("utf-8") if isinstance(payload, str) else payload
            compression = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(name, data, compress_type=compression)
    return buffer.getvalue()


class FakeOcrProvider:
    name = "fake-ocr-ai"

    def cleanup_fragment(self, fragment: str) -> AIOcrCleanupProviderResult:
        return AIOcrCleanupProviderResult(
            text=fragment.replace("Busi- nessAnalysisPlanning", "Business Analysis Planning"),
            confidence=0.94,
            estimated_cost=0.015,
        )


class FakeTocProvider:
    name = "fake-toc-ai"

    def detect_toc(self, context):
        return AiTocProviderResult(
            entries=[
                AiTocCandidate(label="Figure 1. Noise", href="chapter_001.xhtml", confidence=0.98),
                AiTocCandidate(label="Editorial Strategy", href="chapter_001.xhtml", confidence=0.91),
                AiTocCandidate(label="Market Review", href="chapter_002.xhtml", confidence=0.9),
            ],
            confidence=0.9,
            estimated_cost_usd=0.025,
            provider=self.name,
        )


class AIQualityIntelligenceTests(unittest.TestCase):
    def test_clean_book_skips_ai_and_preserves_deterministic_output(self) -> None:
        epub_bytes = _build_epub(
            chapters={
                "chapter_001.xhtml": "<h1>Chapter 1</h1><p>This clean book chapter has normal readable prose.</p>",
                "chapter_002.xhtml": "<h1>Chapter 2</h1><p>Another stable chapter without OCR artifacts.</p>",
            },
            nav_entries=[("Chapter 1", "chapter_001.xhtml"), ("Chapter 2", "chapter_002.xhtml")],
            title="Clean Book",
        )

        report = evaluate_ai_quality_intelligence(epub_bytes)

        self.assertTrue(report["deterministic_output_preserved"])
        self.assertEqual(report["changed_fragment_count"], 0)
        self.assertIn(report["status"], {"skipped", "reported"})
        self.assertEqual(report["estimated_cost_usd"], 0.0)

    def test_ocr_heavy_fixture_records_before_after_without_rewriting_epub(self) -> None:
        epub_bytes = _build_epub(
            chapters={
                "chapter_001.xhtml": (
                    "<h1>OCR Chapter</h1>"
                    "<p>Broken fragment has Busi- nessAnalysisPlanning and OCR junk &#x00c4;.</p>"
                ),
            },
            nav_entries=[("OCR Chapter", "chapter_001.xhtml")],
            title="OCR Heavy",
        )

        report = evaluate_ai_quality_intelligence(
            epub_bytes,
            providers=AIQualityProviders(ocr_cleanup=FakeOcrProvider()),
        )

        self.assertEqual(report["changed_fragment_count"], 1)
        self.assertGreater(report["estimated_cost_usd"], 0.0)
        self.assertTrue(report["deterministic_output_preserved"])
        self.assertEqual(report["score_delta"], 0.0)
        fragment = report["ocr_cleanup"]["fragments"][0]
        self.assertIn("Busi- nessAnalysisPlanning", fragment["before"])
        self.assertIn("Business Analysis Planning", fragment["after"])
        self.assertTrue(fragment["accepted"])

    def test_magazine_low_toc_confidence_uses_ai_candidates_but_rejects_captions(self) -> None:
        epub_bytes = _build_epub(
            chapters={
                "chapter_001.xhtml": "<h1>Editorial Strategy</h1><p>Magazine feature text.</p>",
                "chapter_002.xhtml": "<h1>Market Review</h1><p>More editorial text.</p>",
            },
            nav_entries=[
                ("Advertisement", "chapter_001.xhtml"),
                ("Figure 1. Noise", "chapter_001.xhtml"),
                ("This is a very long paragraph used as a title and should lower deterministic confidence", "chapter_002.xhtml"),
            ],
            title="Magazine",
        )

        report = evaluate_ai_quality_intelligence(
            epub_bytes,
            providers=AIQualityProviders(toc_detection=FakeTocProvider()),
        )

        self.assertEqual(report["toc_detection"]["audit"]["status"], "accepted")
        self.assertEqual(report["changed_toc_entry_count"], 2)
        self.assertEqual(
            report["toc_detection"]["audit"]["rejected_entries"],
            [{"label": "Figure 1. Noise", "reason": "non-content-label"}],
        )

    def test_docx_like_clean_report_does_not_regress_without_provider(self) -> None:
        epub_bytes = _build_epub(
            chapters={
                "chapter_001.xhtml": "<h1>Executive Summary</h1><p>The report is clean and structured.</p>",
                "chapter_002.xhtml": "<h1>Recommendations</h1><p>Action items are readable.</p>",
            },
            nav_entries=[("Executive Summary", "chapter_001.xhtml"), ("Recommendations", "chapter_002.xhtml")],
            title="DOCX Report",
        )

        report = evaluate_ai_quality_intelligence(epub_bytes)

        self.assertEqual(report["changed_fragment_count"], 0)
        self.assertEqual(report["changed_toc_entry_count"], 0)
        self.assertTrue(report["deterministic_output_preserved"])
        self.assertGreaterEqual(report["after_quality_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
