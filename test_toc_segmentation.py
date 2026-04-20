import unittest

from toc_segmentation import normalize_toc_entries, select_section_outline_entries


class TocSegmentationTests(unittest.TestCase):
    def test_prefers_nested_entries_when_top_level_is_mostly_paratext(self):
        raw_toc = [
            (1, "Front Cover", 1),
            (1, "Title", 2),
            (1, "Copyright", 3),
            (1, "Contents", 4),
            (2, "Key to Symbols used", 5),
            (2, "Quick Start Guide", 6),
            (2, "Woodpecker History", 7),
            (2, "General Introduction", 11),
            (2, "1. Easy Exercises", 32),
            (2, "2. Intermediate Exercises", 70),
            (2, "3. Advanced Exercises", 198),
            (1, "Name Index", 381),
            (1, "Sample Record Sheets", 391),
            (1, "Back Cover", 394),
        ]

        outline = select_section_outline_entries(normalize_toc_entries(raw_toc))
        titles = [entry["title"] for entry in outline]

        self.assertIn("Quick Start Guide", titles)
        self.assertIn("3. Advanced Exercises", titles)
        self.assertIn("Back Cover", titles)
        self.assertGreaterEqual(len(outline), 10)

    def test_keeps_top_level_chapters_when_they_are_real_body_sections(self):
        raw_toc = [
            (1, "Chapter 1", 1),
            (2, "1.1 Background", 3),
            (2, "1.2 Method", 8),
            (1, "Chapter 2", 20),
            (2, "2.1 Results", 22),
            (2, "2.2 Discussion", 30),
        ]

        outline = select_section_outline_entries(normalize_toc_entries(raw_toc))
        titles = [entry["title"] for entry in outline]

        self.assertEqual(titles, ["Chapter 1", "Chapter 2"])


if __name__ == "__main__":
    unittest.main()
