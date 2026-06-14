from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz

from chess_study_export import YUSUPOV_CHAPTERS, extract_study_structure, segment_study_pages


def _make_structure_pdf(path: Path) -> None:
    doc = fitz.open()
    for chapter_no, title in YUSUPOV_CHAPTERS:
        page = doc.new_page(width=360, height=240)
        page.insert_text((36, 48), f"{chapter_no} {title}", fontsize=14)
        if chapter_no == 1:
            page.insert_text((36, 84), "Exercises Ex. 1-1 Ex. 1-2 Ex. 1-3", fontsize=10)
    final = doc.new_page(width=360, height=240)
    final.insert_text((36, 48), "Final Test F-1 F-2", fontsize=14)
    appendix = doc.new_page(width=360, height=240)
    appendix.insert_text((36, 48), "Index of Games", fontsize=14)
    books = doc.new_page(width=360, height=240)
    books.insert_text((36, 48), "Recommended Books", fontsize=14)
    doc.save(path)
    doc.close()


class ChessStudyStructureTests(unittest.TestCase):
    def test_extracts_24_chapters_final_test_and_appendices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_structure_pdf(pdf_path)

            structure = extract_study_structure(pdf_path, root / "out")

        self.assertEqual(len(structure["chapters"]), 24)
        self.assertEqual(len([chapter for chapter in structure["chapters"] if chapter["start_book_page"]]), 24)
        self.assertEqual(structure["final_test"]["start_book_page"], 25)
        self.assertEqual(structure["appendices"]["index_of_games"], 26)
        self.assertEqual(structure["appendices"]["recommended_books"], 27)
        self.assertEqual(structure["validation"]["status"], "passed")

    def test_segments_exercises_and_final_test_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "study.pdf"
            _make_structure_pdf(pdf_path)
            structure = extract_study_structure(pdf_path, root / "out")

            segments = segment_study_pages(pdf_path, structure, root / "out")

        first = segments["pages"][0]
        final = segments["pages"][24]
        self.assertEqual(first["page_type"], "exercises")
        self.assertIn("Ex. 1-1", first["exercise_labels"])
        self.assertEqual(final["page_type"], "final_test")
        self.assertIn("F-1", final["final_test_labels"])

    def test_structure_uses_html_contents_page_when_pdf_text_is_sparse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "scan-like.pdf"
            doc = fitz.open()
            for _ in range(266):
                doc.new_page(width=360, height=240)
            doc.save(pdf_path)
            doc.close()
            html_path = root / "current.html"
            html_path.write_text(
                '<section class="chess-book-page" data-page="4">'
                'Key to symbols used Preface Introduction '
                'Mating motifs 1 Mating motifs 2 2 Basic opening principles 3 Simple pawn endings 4 '
                'Double check 5 The value of the pieces 6 The discovered attack 7 Centralizing the pieces 8 '
                'Mate in two moves 9 The opposition 10 The pin 11 The double attack 12 '
                'Realizing a material advantage 13 Open files and Outposts 14 Combinations 15 '
                'Queen against pawn 16 Stalemate motifs 17 Forced variations 18 '
                'Combinations involving promotion 19 Weak points 20 Pawn combinations 21 '
                'The wrong bishop 22 Smothered mate 23 Gambits 24 Final test Appendices '
                'Index of composers and analysts Index of games Recommended books '
                'CONTENTS 4 5 6 8 18 30 44 54 64 74 82 92 100 110 120 128 138 148 156 164 172 182 192 202 212 222 232 244 252 254 262'
                '</section>',
                encoding="utf-8",
            )

            structure = extract_study_structure(pdf_path, root / "out", html_path=html_path)

        self.assertEqual(structure["structure_text_source"], "html_toc_assist")
        self.assertEqual(len([chapter for chapter in structure["chapters"] if chapter["start_book_page"]]), 24)
        self.assertEqual(structure["chapters"][0]["start_book_page"], 8)
        self.assertEqual(structure["chapters"][23]["start_book_page"], 232)
        self.assertEqual(structure["final_test"]["start_book_page"], 244)
        self.assertEqual(structure["appendices"]["recommended_books"], 262)


if __name__ == "__main__":
    unittest.main()
