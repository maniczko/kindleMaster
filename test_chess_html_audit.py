from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from scripts.audit_chess_html import audit_chess_html


class ChessHtmlAuditTests(unittest.TestCase):
    def test_audit_reports_page_mismatch_localhost_and_empty_copy_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            doc = fitz.open()
            doc.new_page(width=200, height=200)
            doc.new_page(width=200, height=200)
            doc.save(pdf_path)
            doc.close()

            html_path = root / "book.html"
            html_path.write_text(
                """
                <!doctype html><html><body>
                  <section class="chess-book-page" data-page="1" style="width:200px;height:200px">
                    <a href="http://localhost:5001/debug">debug</a>
                    <button class="copy-pgn-button" data-copy-target="missing">Copy PGN</button>
                  </section>
                </body></html>
                """,
                encoding="utf-8",
            )

            report = audit_chess_html(pdf_path, html_path, output=root / "conversion_audit.json")

        self.assertEqual(report["status"], "failed")
        self.assertIn("pdf_html_page_count_mismatch", report["critical_errors"])
        self.assertIn("missing_pdf_pages_in_html", report["critical_errors"])
        self.assertIn("localhost_links_present", report["critical_errors"])
        self.assertIn("empty_or_inactive_copy_buttons", report["critical_errors"])
        self.assertEqual(report["missing_pages"], [2])

    def test_audit_accepts_clean_complete_minimal_book_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            doc = fitz.open()
            doc.new_page(width=200, height=200)
            doc.save(pdf_path)
            doc.close()

            html_path = root / "book.html"
            html_path.write_text(
                """
                <!doctype html><html><body>
                  <section class="chess-book-page" data-page="1" style="width:200px;height:200px">
                    <div class="book-fen" style="left:10px;top:10px;width:100px;height:20px">
                      FEN: <code id="fen1">8/8/8/8/8/8/4K3/4k3 w - - 0 1</code>
                    </div>
                    <button class="copy-fen-button" data-copy-target="fen1">Copy FEN</button>
                  </section>
                </body></html>
                """,
                encoding="utf-8",
            )

            report = audit_chess_html(pdf_path, html_path, output=root / "conversion_audit.json")

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["pdf_pages"], 1)
        self.assertEqual(report["html_pages"], 1)
        self.assertEqual(report["fen"]["valid"], 1)
        self.assertEqual(report["copy_buttons"]["fen"], 1)


if __name__ == "__main__":
    unittest.main()
