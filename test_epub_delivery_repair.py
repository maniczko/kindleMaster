from __future__ import annotations

import io
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from epub_delivery_repair import has_progressive_jpeg, repair_epub_for_delivery


def _jpeg_bytes(*, progressive: bool) -> bytes:
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover
        raise unittest.SkipTest("Pillow is required for JPEG repair tests") from error

    image = Image.new("RGB", (900, 1400), (230, 230, 220))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90, progressive=progressive)
    return output.getvalue()


def _epub_with_jpeg(jpeg: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:delivery-repair</dc:identifier>
    <dc:title>Delivery Repair Sample</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:language>en</dc:language>
    <dc:publisher>KindleMaster</dc:publisher>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="photo" href="images/photo.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><title>Delivery Repair Sample</title></head>
<body><nav epub:type="toc"><ol><li><a href="chapter.xhtml">Chapter</a></li></ol></nav></body></html>""",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>Chapter</title></head>
<body><h1>Chapter</h1><p>Clean article text for Kindle delivery.</p><img src="images/photo.jpg" alt="Photo"/></body></html>""",
        )
        archive.writestr("OEBPS/images/photo.jpg", jpeg)
    return buffer.getvalue()


class EpubDeliveryRepairTests(unittest.TestCase):
    def test_reencodes_progressive_jpeg_to_baseline_candidate(self) -> None:
        source_epub = _epub_with_jpeg(_jpeg_bytes(progressive=True))

        result = repair_epub_for_delivery(source_epub, run_package_recovery=False)

        self.assertEqual(result.status, "applied")
        self.assertIn("reencode_progressive_jpeg", result.actions)
        self.assertFalse(has_progressive_jpeg(result.epub_bytes))

    def test_rejects_candidate_when_quality_selection_keeps_baseline(self) -> None:
        source_epub = _epub_with_jpeg(_jpeg_bytes(progressive=True))
        report = {
            "status": "rejected",
            "selected_candidate": "active",
            "rejected_candidate": "auto_repair",
            "selected_stage": "active",
            "rejected_stage": "auto_repair",
            "reason_codes": ["quality_monotonic_regression"],
            "candidates": [],
        }
        selection = SimpleNamespace(selected_bytes=source_epub, report=report)

        with patch("epub_quality_selection.select_epub_by_quality", return_value=selection):
            result = repair_epub_for_delivery(source_epub, run_package_recovery=False)

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.epub_bytes, source_epub)
        self.assertEqual(result.quality_selection["rejected_candidate"], "auto_repair")


if __name__ == "__main__":
    unittest.main()
