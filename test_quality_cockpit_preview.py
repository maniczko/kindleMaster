from __future__ import annotations

import io
import json
import unittest
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from quality_cockpit_preview import (
    build_asset_summary,
    build_epubcheck_detail,
    build_metadata_summary,
    build_quality_cockpit_preview,
    build_toc_preview,
)


def _tiny_epub_bytes() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles>
                <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/package.opf",
            """<?xml version="1.0"?>
            <package version="3.0" xmlns="http://www.idpf.org/2007/opf">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>Archive Title</dc:title>
                <dc:creator>Archive Author</dc:creator>
                <dc:language>en</dc:language>
              </metadata>
              <manifest>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
                <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
                <item id="img1" href="images/diagram-board.png" media-type="image/png"/>
                <item id="img2" href="images/photo.jpg" media-type="image/jpeg"/>
              </manifest>
              <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
            </package>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml">
              <body>
                <nav epub:type="toc">
                  <ol>
                    <li><a href="chapter1.xhtml">Opening</a></li>
                    <li><a href="chapter2.xhtml#part">Second Part</a></li>
                  </ol>
                </nav>
              </body>
            </html>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/chapter1.xhtml",
            """<html><body>
              <h1>Opening</h1>
              <img src="images/diagram-board.png"/>
              <img src="images/photo.jpg"/>
              <img src="images/photo.jpg"/>
            </body></html>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/chapter2.xhtml",
            """<html><body><h1>Second Part</h1></body></html>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr("EPUB/images/diagram-board.png", b"\x89PNG" + (b"a" * 120))
        archive.writestr("EPUB/images/photo.jpg", b"\xff\xd8" + (b"b" * 80))
    return buffer.getvalue()


def _image_bytes(size: tuple[int, int], *, image_format: str, progressive: bool = False) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    image = Image.new("RGB", size, color=(240, 240, 240))
    save_kwargs = {"format": image_format}
    if image_format.upper() == "JPEG":
        save_kwargs["progressive"] = progressive
    image.save(buffer, **save_kwargs)
    return buffer.getvalue()


def _media_risk_epub_bytes() -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles>
                <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
              </rootfiles>
            </container>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/package.opf",
            """<?xml version="1.0"?>
            <package version="3.0" xmlns="http://www.idpf.org/2007/opf">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>Media Risk Fixture</dc:title>
                <dc:creator>Fixture Author</dc:creator>
                <dc:language>en</dc:language>
                <meta name="cover" content="cover-image"/>
              </metadata>
              <manifest>
                <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
                <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
                <item id="photo" href="images/photo.jpg" media-type="image/jpeg"/>
                <item id="icon" href="images/icon.png" media-type="image/png"/>
                <item id="vector" href="images/vector.svg" media-type="image/svg+xml"/>
                <item id="audio" href="media/note.mp3" media-type="audio/mpeg"/>
              </manifest>
              <spine><itemref idref="chapter"/></spine>
            </package>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body><nav epub:type="toc">
            <ol><li><a href="chapter.xhtml">Start</a></li></ol></nav></body></html>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "EPUB/chapter.xhtml",
            """<html><body>
            <img src="images/cover.jpg"/>
            <img src="images/photo.jpg"/>
            <img src="images/icon.png"/>
            <img src="images/vector.svg"/>
            </body></html>""",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr("EPUB/images/cover.jpg", _image_bytes((500, 500), image_format="JPEG"))
        archive.writestr("EPUB/images/photo.jpg", _image_bytes((900, 1400), image_format="JPEG", progressive=True))
        archive.writestr("EPUB/images/icon.png", _image_bytes((240, 220), image_format="PNG"))
        archive.writestr("EPUB/images/vector.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
        archive.writestr("EPUB/media/note.mp3", b"synthetic audio payload")
    return buffer.getvalue()


class QualityCockpitPreviewTests(unittest.TestCase):
    def test_build_quality_cockpit_preview_uses_archive_fallbacks(self) -> None:
        preview = build_quality_cockpit_preview(epub_bytes=_tiny_epub_bytes())

        self.assertEqual(preview["toc_preview"]["entry_count"], 2)
        self.assertEqual(
            preview["toc_preview"]["entries"],
            [
                {"title": "Opening", "href": "EPUB/chapter1.xhtml"},
                {"title": "Second Part", "href": "EPUB/chapter2.xhtml#part"},
            ],
        )
        self.assertEqual(preview["asset_summary"]["image_count"], 2)
        self.assertEqual(preview["asset_summary"]["total_image_bytes"], 206)
        self.assertEqual(preview["asset_summary"]["largest_assets"][0]["path"], "EPUB/images/diagram-board.png")
        self.assertEqual(preview["asset_summary"]["diagram_chess"]["diagram_count"], 1)
        self.assertEqual(preview["asset_summary"]["diagram_chess"]["chess_diagram_count"], 1)
        self.assertEqual(preview["asset_summary"]["duplicate_src_count"], 1)
        self.assertEqual(preview["asset_summary"]["asset_budget_status"], "not_reported")
        self.assertEqual(preview["asset_summary"]["unsupported_media_count"], 0)
        self.assertEqual(preview["asset_summary"]["script_count"], 0)
        self.assertEqual(preview["metadata_summary"]["title"], "Archive Title")
        self.assertEqual(preview["metadata_summary"]["creator"], "Archive Author")
        self.assertEqual(preview["metadata_summary"]["language"], "en")
        self.assertEqual(preview["metadata_summary"]["placeholders_detected"], [])
        json.dumps(preview)

    def test_metadata_and_quality_report_are_preferred_over_archive_when_available(self) -> None:
        metadata = {
            "title": "Runtime Title",
            "creator": "Runtime Author",
            "language": "pl",
            "heading_repair": {"toc_before": 1, "toc_after": 4},
            "toc_entries": [{"title": "Runtime Chapter", "href": "chapter.xhtml"}],
            "largest_assets": [{"path": "runtime/image.png", "bytes": 500}],
            "archive_image_count": 7,
            "total_image_bytes": 999,
            "diagram_count": 3,
            "chess_diagram_count": 2,
        }
        quality_report = {
            "validation_status": "failed",
            "validation_tool": "epubcheck",
            "epubcheck": {
                "status": "failed",
                "tool": "epubcheck",
                "messages": [
                    {"severity": "ERROR", "message": "Bad href"},
                    {"severity": "WARNING", "message": "Minor metadata issue"},
                ],
            },
        }

        self.assertEqual(
            build_toc_preview(metadata, quality_report=quality_report, epub_bytes=_tiny_epub_bytes()),
            {
                "entry_count": 4,
                "entries": [{"title": "Runtime Chapter", "href": "chapter.xhtml"}],
                "warnings": [],
                "toc_before": 1,
                "toc_after": 4,
            },
        )
        self.assertEqual(
            build_asset_summary(metadata, quality_report=quality_report, epub_bytes=_tiny_epub_bytes()),
            {
                "image_count": 7,
                "largest_assets": [{"path": "runtime/image.png", "bytes": 500}],
                "total_image_bytes": 999,
                "diagram_chess": {"diagram_count": 3, "chess_diagram_count": 2},
                "oversize_count": 0,
                "duplicate_src_count": 1,
                "asset_budget_status": "not_reported",
                "unsupported_media_count": 0,
                "script_count": 0,
                "media_risk_count": 0,
                "image_quality": {
                    "status": "passed",
                    "inspected_image_count": 0,
                    "cover": {
                        "status": "not_reported",
                        "path": "",
                        "width": None,
                        "height": None,
                        "aspect_ratio": None,
                        "bytes": None,
                        "issues": [],
                    },
                    "low_resolution_count": 0,
                    "low_resolution_images": [],
                    "progressive_jpeg_count": 0,
                    "progressive_jpeg_images": [],
                    "media_risk_count": 0,
                    "media_risks": [],
                },
            },
        )
        self.assertEqual(
            build_epubcheck_detail(metadata, quality_report=quality_report),
            {
                "status": "failed",
                "tool": "epubcheck",
                "error_count": 1,
                "warning_count": 1,
                "messages": ["Bad href", "Minor metadata issue"],
            },
        )
        self.assertEqual(
            build_metadata_summary(metadata, quality_report=quality_report, epub_bytes=_tiny_epub_bytes()),
            {
                "title": "Runtime Title",
                "creator": "Runtime Author",
                "language": "pl",
                "placeholders_detected": [],
            },
        )

    def test_invalid_epub_bytes_return_neutral_json_serializable_summaries(self) -> None:
        preview = build_quality_cockpit_preview(
            {"title": "Untitled", "creator": "Unknown Author", "language": ""},
            epub_bytes=b"not a zip",
        )

        self.assertEqual(preview["toc_preview"]["entry_count"], 0)
        self.assertEqual(preview["asset_summary"]["image_count"], 0)
        self.assertEqual(preview["asset_summary"]["largest_assets"], [])
        self.assertEqual(preview["epubcheck_detail"]["status"], "unavailable")
        self.assertEqual(preview["metadata_summary"]["placeholders_detected"], ["title", "creator", "language"])
        json.dumps(preview)

    def test_archive_asset_summary_reports_image_quality_and_media_risks(self) -> None:
        summary = build_asset_summary(epub_bytes=_media_risk_epub_bytes())

        self.assertEqual(summary["image_count"], 4)
        self.assertEqual(summary["unsupported_media_count"], 2)
        self.assertEqual(summary["media_risk_count"], 2)
        image_quality = summary["image_quality"]
        self.assertEqual(image_quality["status"], "failed")
        self.assertEqual(image_quality["inspected_image_count"], 3)
        self.assertEqual(image_quality["cover"]["path"], "EPUB/images/cover.jpg")
        self.assertEqual(image_quality["cover"]["status"], "failed")
        self.assertIn("cover_aspect_ratio", image_quality["cover"]["issues"])
        self.assertIn("cover_resolution", image_quality["cover"]["issues"])
        self.assertEqual(image_quality["low_resolution_count"], 2)
        self.assertEqual(image_quality["progressive_jpeg_count"], 1)
        self.assertEqual(image_quality["progressive_jpeg_images"][0]["path"], "EPUB/images/photo.jpg")
        self.assertEqual(image_quality["media_risk_count"], 2)
        json.dumps(summary)


if __name__ == "__main__":
    unittest.main()
