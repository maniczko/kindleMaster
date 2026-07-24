import json
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from chess_full_publication import (
    FullChessPublicationError,
    _notation_text_candidates,
    publish_full_chess_publication,
)


class ChessFullPublicationTests(unittest.TestCase):
    def test_notation_section_excludes_non_chess_page_prose(self) -> None:
        soup = BeautifulSoup(
            """
            <pre class="chess-notation-page" data-page="10">
            German edition
            All sales or enquiries should be directed to the publisher.
            22.Bd5+
            23.Rxe6+! fxe6 24.Qxe6+
            White wins after the forced variation.
            33...e3! 34.Qxe3
            </pre>
            """,
            "html.parser",
        )

        self.assertEqual(
            _notation_text_candidates(soup.pre),
            [
                "22.Bd5+\n23.Rxe6+! fxe6 24.Qxe6+\n33...e3! 34.Qxe3",
            ],
        )

    def test_enriches_diagram_without_losing_book_text_or_notation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output" / "source.epub"
            source.parent.mkdir(parents=True)
            self._write_epub(source)
            self._write_pdf(root / "input" / "source.pdf", page_count=10)
            render = root / "semantic_chess_html" / "assets" / "verified_fen" / "board.svg"
            render.parent.mkdir(parents=True)
            render.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640"></svg>',
                encoding="utf-8",
            )
            pgn = root / "report" / "chess_games.pgn"
            pgn.parent.mkdir(parents=True)
            pgn.write_text(
                "\n".join(
                    [
                        '[Site "?"]',
                        '[Date "2026.07.23"]',
                        '[Round "?"]',
                        '[White "White"]',
                        '[Black "Black"]',
                        '[Result "*"]',
                        "",
                        "1. e4 e5 *",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            report = publish_full_chess_publication(
                source_epub=source,
                output_epub=source,
                reader_dir=root / "semantic_chess_html",
                verified_records=[
                    {
                        "id": "layout-chess-p010-d01",
                        "page_number": 10,
                        "bbox": [72.0, 96.0, 216.0, 240.0],
                        "confirmed_diagram": True,
                        "board_crop_path": (
                            "review/chess_fen/two_crop/"
                            "notation_layout_p010_01_board.png"
                        ),
                        "publication_included": True,
                        "fen_human_verified": True,
                        "placement_human_verified": True,
                        "full_fen": (
                            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/"
                            "RNBQKBNR w KQkq - 0 2"
                        ),
                        "side_to_move": "w",
                        "verified_render_path": (
                            "semantic_chess_html/assets/verified_fen/board.svg"
                        ),
                    }
                ],
                artifact_root=root,
                accepted_pgn_path=pgn,
            )

            self.assertTrue(report["summary"]["source_text_preserved"])
            self.assertEqual(report["summary"]["fen_human_verified"], 1)
            self.assertEqual(report["summary"]["accepted_pgn"], 1)
            with zipfile.ZipFile(source) as archive:
                chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")
                package = archive.read("EPUB/package.opf").decode("utf-8")
                self.assertIn("This lesson explains the mating pattern.", chapter)
                self.assertIn("23.Rxe6+! fxe6 24.Qxe6+", chapter)
                self.assertIn('data-diagram-id="layout-chess-p010-d01"', chapter)
                self.assertIn('data-fen-source="human_verified"', chapter)
                self.assertIn("Human verified FEN:", chapter)
                self.assertIn("images/verified_fen/", package)
                self.assertIn("supplements/chess_games.pgn", package)
                self.assertTrue(
                    any(name.endswith(".svg") for name in archive.namelist())
                )
            reader = (root / "semantic_chess_html" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("This lesson explains the mating pattern.", reader)
            self.assertIn("23.Rxe6+! fxe6 24.Qxe6+", reader)
            self.assertIn('id="doc-001-lesson"', reader)
            self.assertIn('href="#doc-001-lesson"', reader)
            self.assertIn('id="pdf-pages"', reader)
            self.assertIn('id="book-text"', reader)
            self.assertIn('id="notation"', reader)
            self.assertIn('id="pgn"', reader)
            self.assertIn("Kopiuj tekst notacji", reader)
            self.assertIn("Kopiuj wszystkie PGN", reader)
            reader_script = (
                root / "semantic_chess_html" / "reader.js"
            ).read_text(encoding="utf-8")
            self.assertIn(
                "try{await navigator.clipboard.writeText(value);return}catch{}",
                reader_script,
            )
            self.assertIn("area.focus({preventScroll:true})", reader_script)
            self.assertIn(
                "area.setSelectionRange(0,area.value.length)",
                reader_script,
            )
            self.assertIn("Pokaż stronę PDF i położenie diagramu", reader)
            self.assertIn('href="#pdf-page-0010"', reader)
            self.assertIn('data-diagram-id="layout-chess-p010-d01"', reader)
            self.assertTrue(
                (root / "semantic_chess_html" / "pages" / "page-0010.webp").is_file()
            )
            reader_soup = BeautifulSoup(reader, "html.parser")
            notation_pgn = reader_soup.select_one("#notation .copy-pgn")
            accepted_pgn_copy = reader_soup.select_one("#pgn [data-copy-target]")
            self.assertIsNotNone(notation_pgn)
            self.assertTrue(notation_pgn.has_attr("disabled"))
            self.assertIsNotNone(accepted_pgn_copy)
            self.assertFalse(accepted_pgn_copy.has_attr("disabled"))
            reader_ids = [
                str(node.get("id"))
                for node in reader_soup.select("[id]")
                if node.get("id")
            ]
            self.assertFalse(
                {
                    node_id: count
                    for node_id, count in Counter(reader_ids).items()
                    if count > 1
                }
            )
            manifest = json.loads(
                (
                    root
                    / "semantic_chess_html"
                    / "data"
                    / "artifact_manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["pipeline_mode"],
                "full_epub_enriched_reader",
            )
            self.assertEqual(manifest["summary"]["pdf_page_count"], 10)
            self.assertEqual(manifest["summary"]["diagram_overlay_count"], 1)
            self.assertEqual(manifest["summary"]["notation_blocker_count"], 1)

            publish_full_chess_publication(
                source_epub=source,
                output_epub=source,
                reader_dir=root / "semantic_chess_html",
                verified_records=[
                    {
                        "id": "layout-chess-p010-d01",
                        "page_number": 10,
                        "bbox": [72.0, 96.0, 216.0, 240.0],
                        "confirmed_diagram": True,
                        "board_crop_path": (
                            "review/chess_fen/two_crop/"
                            "notation_layout_p010_01_board.png"
                        ),
                        "publication_included": True,
                        "fen_human_verified": True,
                        "placement_human_verified": True,
                        "full_fen": (
                            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/"
                            "RNBQKBNR w KQkq - 0 2"
                        ),
                        "side_to_move": "w",
                        "verified_render_path": (
                            "semantic_chess_html/assets/verified_fen/board.svg"
                        ),
                    }
                ],
                artifact_root=root,
                accepted_pgn_path=pgn,
            )
            with zipfile.ZipFile(source) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), len(set(names)))
                package = archive.read("EPUB/package.opf").decode("utf-8")
                self.assertEqual(package.count('href="images/verified_fen/'), 1)
                self.assertEqual(
                    package.count('href="supplements/chess_games.pgn"'),
                    1,
                )

    def test_reader_prefers_complete_source_bound_notation_and_keeps_epub_audit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output" / "source.epub"
            source.parent.mkdir(parents=True)
            self._write_text_only_epub(source)
            self._write_pdf(root / "input" / "source.pdf", page_count=10)
            source_decode = {
                "schema": "kindlemaster.source_bound_chess_notation.v1",
                "source_pdf": str(root / "input" / "source.pdf"),
                "source_pdf_sha256": "a" * 64,
                "pages": {
                    "10": {
                        "page_number": 10,
                        "status": "decoded",
                        "decoded_text": "1.Rd8+ Kg7 2.R1d7+ Kf6",
                        "blockers": [],
                        "lines": [],
                    }
                },
            }

            with patch(
                "chess_full_publication.extract_source_notation_pages",
                return_value=source_decode,
            ):
                report = publish_full_chess_publication(
                    source_epub=source,
                    output_epub=source,
                    reader_dir=root / "semantic_chess_html",
                    verified_records=[],
                    artifact_root=root,
                )

            reader = (root / "semantic_chess_html" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("1.Rd8+ Kg7 2.R1d7+ Kf6", reader)
            self.assertIn('data-decoding-source="source_font_sha_gid"', reader)
            reader_soup = BeautifulSoup(reader, "html.parser")
            notation_pgn = reader_soup.select_one("#notation .copy-pgn")
            self.assertIsNotNone(notation_pgn)
            self.assertTrue(notation_pgn.has_attr("disabled"))
            audit = json.loads(
                (
                    root
                    / "semantic_chess_html"
                    / "reports"
                    / "source_notation_decode.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                audit["epub_fragments"][0]["epub_text"],
                "23.Rxe6+! fxe6 24.Qxe6+",
            )
            self.assertEqual(
                report["summary"]["source_decoded_notation_fragments"],
                1,
            )
            health = json.loads(
                (
                    root
                    / "semantic_chess_html"
                    / "reports"
                    / "final_reader_health_gate.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn(
                "source_notation_review_required",
                health["warnings"],
            )

    def test_blocks_incomplete_mapping_instead_of_publishing_partial_book(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output" / "source.epub"
            source.parent.mkdir(parents=True)
            self._write_epub(source)
            with self.assertRaisesRegex(
                FullChessPublicationError,
                "verified_diagram_mapping_incomplete",
            ):
                publish_full_chess_publication(
                    source_epub=source,
                    output_epub=source,
                    reader_dir=root / "semantic_chess_html",
                    verified_records=[
                        {
                            "id": "layout-chess-p099-d01",
                            "page_number": 99,
                            "board_crop_path": (
                                "review/chess_fen/two_crop/"
                                "notation_layout_p099_01_board.png"
                            ),
                            "publication_included": True,
                            "fen_human_verified": True,
                            "placement_human_verified": True,
                            "full_fen": (
                                "8/8/8/8/8/8/4K3/7k w - - 0 1"
                            ),
                            "verified_render_path": (
                                "semantic_chess_html/assets/verified_fen/missing.svg"
                            ),
                        }
                    ],
                    artifact_root=root,
                )

    def test_inserts_confirmed_diagrams_at_source_page_without_replacing_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output" / "source.epub"
            source.parent.mkdir(parents=True)
            self._write_text_only_epub(source)
            render = root / "assets" / "verified_fen" / "verified.svg"
            render.parent.mkdir(parents=True)
            render.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="640"></svg>',
                encoding="utf-8",
            )
            crop = (
                root
                / "review"
                / "chess_fen"
                / "two_crop"
                / "notation_layout_p010_02_board.png"
            )
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"\x89PNG\r\n\x1a\nsource-board")

            report = publish_full_chess_publication(
                source_epub=source,
                output_epub=source,
                reader_dir=root / "semantic_chess_html",
                verified_records=[
                    {
                        "id": "layout-chess-p010-d01",
                        "page_number": 10,
                        "source_order": 1,
                        "publication_included": True,
                        "confirmed_diagram": True,
                        "fen_human_verified": True,
                        "placement_human_verified": True,
                        "full_fen": "8/8/8/8/8/8/4K3/7k w - - 0 1",
                        "side_to_move": "w",
                        "verified_render_path": "assets/verified_fen/verified.svg",
                    },
                    {
                        "id": "layout-chess-p010-d02",
                        "page_number": 10,
                        "source_order": 2,
                        "publication_included": True,
                        "confirmed_diagram": True,
                        "fen_human_verified": False,
                        "placement_human_verified": False,
                        "board_crop_path": (
                            "review/chess_fen/two_crop/"
                            "notation_layout_p010_02_board.png"
                        ),
                    },
                    {
                        "id": "layout-chess-p010-d03",
                        "page_number": 10,
                        "publication_included": False,
                        "confirmed_diagram": False,
                    },
                ],
                artifact_root=root,
            )

            self.assertEqual(report["summary"]["diagrams_total"], 2)
            self.assertEqual(report["summary"]["fen_human_verified"], 1)
            self.assertEqual(report["summary"]["unreadable_source_diagram_crops"], 1)
            with zipfile.ZipFile(source) as archive:
                chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")
                self.assertIn("The complete lesson text remains here.", chapter)
                self.assertIn("23.Rxe6+! fxe6 24.Qxe6+", chapter)
                self.assertIn("layout-chess-p010-d01", chapter)
                self.assertIn("layout-chess-p010-d02", chapter)
                self.assertNotIn("layout-chess-p010-d03", chapter)
                self.assertIn('data-fen-status="unreadable"', chapter)
                self.assertTrue(
                    any(
                        name.startswith("EPUB/images/source_diagrams/")
                        for name in archive.namelist()
                    )
                )

    def test_rejects_position_only_pgn_as_book_notation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "output" / "source.epub"
            source.parent.mkdir(parents=True)
            self._write_epub(source)
            pgn = root / "report" / "chess_verified_positions.pgn"
            pgn.parent.mkdir(parents=True)
            pgn.write_text(
                "\n".join(
                    [
                        '[Event "Verified position"]',
                        '[Site "?"]',
                        '[Date "2026.07.23"]',
                        '[Round "?"]',
                        '[White "?"]',
                        '[Black "?"]',
                        '[Result "*"]',
                        '[SetUp "1"]',
                        '[FEN "8/8/8/8/8/8/4K3/7k w - - 0 1"]',
                        "",
                        "*",
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FullChessPublicationError,
                "accepted_pgn_position_only",
            ):
                publish_full_chess_publication(
                    source_epub=source,
                    output_epub=source,
                    reader_dir=root / "semantic_chess_html",
                    verified_records=[],
                    artifact_root=root,
                    accepted_pgn_path=pgn,
                )

    @staticmethod
    def _write_epub(path: Path) -> None:
        container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="EPUB/package.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
        package = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier>book</dc:identifier>
    <dc:title>Complete Chess Book</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter_001.xhtml"
      media-type="application/xhtml+xml"/>
    <item id="diagram" href="images/notation_layout_p010_01.png"
      media-type="image/png"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>"""
        chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Lesson</title></head>
<body><h1 id="lesson">Mating motifs</h1>
<p>This lesson explains the mating pattern.</p>
<div class="figure chess-diagram-container">
<img class="chess-diagram" src="images/notation_layout_p010_01.png"
 alt="Chess diagram"/></div>
<p class="notation-heavy">23.Rxe6+! fxe6 24.Qxe6+</p>
<p><a href="#lesson">Back to lesson</a></p>
<p>The explanation after the variation is also preserved.</p>
</body></html>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "mimetype",
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr("META-INF/container.xml", container)
            archive.writestr("EPUB/package.opf", package)
            archive.writestr("EPUB/chapter_001.xhtml", chapter)
            archive.writestr(
                "EPUB/images/notation_layout_p010_01.png",
                b"\x89PNG\r\n\x1a\nfixture",
            )

    @staticmethod
    def _write_text_only_epub(path: Path) -> None:
        container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="EPUB/package.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
        package = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier>book</dc:identifier>
    <dc:title>Complete Chess Book</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter_001.xhtml"
      media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>"""
        chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Lesson</title></head>
<body><h1>Mating motifs</h1>
<p>The complete lesson text remains here.</p>
<pre class="chess-notation-page" data-page="10"><code>23.Rxe6+! fxe6 24.Qxe6+</code></pre>
<p>The explanation after the source page is also preserved.</p>
</body></html>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "mimetype",
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr("META-INF/container.xml", container)
            archive.writestr("EPUB/package.opf", package)
            archive.writestr("EPUB/chapter_001.xhtml", chapter)

    @staticmethod
    def _write_pdf(path: Path, *, page_count: int) -> None:
        import fitz

        path.parent.mkdir(parents=True, exist_ok=True)
        document = fitz.open()
        try:
            for page_number in range(1, page_count + 1):
                page = document.new_page(width=468, height=678)
                page.insert_text((36, 42), f"Source page {page_number}")
                if page_number == page_count:
                    page.draw_rect(
                        fitz.Rect(72, 96, 216, 240),
                        color=(0, 0, 0),
                        width=1,
                    )
            document.save(path)
        finally:
            document.close()


if __name__ == "__main__":
    unittest.main()
