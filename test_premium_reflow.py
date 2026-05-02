import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from converter import ConversionConfig
from premium_reflow import _extract_lines_from_page, _repair_mojibake, extract_book_premium, pdfplumber


class PremiumReflowGeneralizationTests(unittest.TestCase):
    def test_repair_mojibake_repairs_generic_registered_mark_suffix(self):
        self.assertEqual(_repair_mojibake("ACME\u0139\u02dd guide"), "ACME\u00ae guide")

    def test_repair_mojibake_preserves_plain_text_without_sample_specific_dependency(self):
        self.assertEqual(_repair_mojibake("Omega report"), "Omega report")

    def test_extract_lines_orders_two_column_pages_by_column_before_row(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=760)
        for index, text in enumerate(["Left one", "Left two", "Left three"]):
            page.insert_text((72, 96 + index * 32), text, fontsize=10)
        for index, text in enumerate(["Right one", "Right two", "Right three"]):
            page.insert_text((340, 96 + index * 32), text, fontsize=10)

        lines = [line.text.strip() for line in _extract_lines_from_page(page, 0)]
        doc.close()

        self.assertEqual(
            lines[:6],
            ["Left one", "Left two", "Left three", "Right one", "Right two", "Right three"],
        )

    def test_extract_book_premium_reports_reading_order_for_multicolumn_pages(self):
        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "two-column-handbook.pdf"
            _build_two_column_handbook(pdf_path)

            content = extract_book_premium(
                str(pdf_path),
                config=ConversionConfig(language="en"),
                pdf_metadata={"title": "Two Column Handbook", "author": "QA"},
            )

        reading_flow = content["metadata"]["reading_flow"]
        html = "\n".join(part for chapter in content["chapters"] for part in chapter.get("html_parts", []))
        left_index = html.index("Left one")
        right_index = html.index("Right one")

        self.assertEqual(reading_flow["status"], "passed")
        self.assertGreaterEqual(reading_flow["confidence"], 0.9)
        self.assertEqual(reading_flow["estimated_multi_column_pages"], 1)
        self.assertEqual(reading_flow["low_confidence_region_count"], 0)
        self.assertLess(left_index, right_index)

    @unittest.skipIf(pdfplumber is None, "pdfplumber is required for document-like report gating")
    def test_captioned_vector_diagram_survives_document_like_report_path(self):
        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "dense-handbook.pdf"
            _build_dense_handbook_with_captioned_vector_diagram(pdf_path)

            content = extract_book_premium(
                str(pdf_path),
                config=ConversionConfig(language="en"),
                pdf_metadata={"title": "Dense Handbook", "author": "QA"},
            )

        html = "\n".join(part for chapter in content["chapters"] for part in chapter.get("html_parts", []))
        images = content.get("images", [])

        self.assertTrue(content["metadata"]["document_like_report"])
        self.assertEqual(len(images), 1)
        self.assertLess(len(images[0]["data"]), 250_000)
        self.assertIn('class="figure premium-figure technical-figure"', html)
        self.assertIn('src="images/diagram_p2_1.png"', html)
        self.assertIn('alt="Figure 3.0.1 Input/Output Diagram"', html)
        self.assertIn("<figcaption>Figure 3.0.1 Input/Output Diagram</figcaption>", html)
        self.assertNotIn("<p>Figure 3.0.1 Input/Output Diagram</p>", html)


def _build_dense_handbook_with_captioned_vector_diagram(path: Path) -> None:
    doc = fitz.open()
    for page_number in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 64), f"Section {page_number + 1}", fontsize=16)
        page.insert_text((72, 96), "Dense handbook prose with structured content.", fontsize=10)

    table_page = doc[0]
    x0, y0 = 72, 150
    col_width = 100
    row_height = 26
    rows = [["Input", "Owner", "Status"], ["Request", "Analyst", "Ready"], ["Output", "Team", "Done"]]
    for row_index in range(len(rows) + 1):
        y = y0 + row_index * row_height
        table_page.draw_line((x0, y), (x0 + len(rows[0]) * col_width, y), width=1)
    for column_index in range(len(rows[0]) + 1):
        x = x0 + column_index * col_width
        table_page.draw_line((x, y0), (x, y0 + len(rows) * row_height), width=1)
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            table_page.insert_text(
                (x0 + column_index * col_width + 6, y0 + row_index * row_height + 17),
                value,
                fontsize=8,
            )

    figure_page = doc[1]
    figure_page.insert_text((72, 134), "Figure 3.0.1 Input/Output Diagram", fontsize=10)
    left_box = fitz.Rect(124, 172, 254, 236)
    center_box = fitz.Rect(304, 172, 434, 236)
    output_box = fitz.Rect(214, 294, 344, 358)
    for rect in (left_box, center_box, output_box):
        figure_page.draw_rect(rect, color=(0, 0, 0), width=1.2)
    figure_page.draw_line((254, 204), (304, 204), color=(0, 0, 0), width=1.2)
    figure_page.draw_line((369, 236), (279, 294), color=(0, 0, 0), width=1.2)
    figure_page.insert_text((154, 207), "Input", fontsize=9)
    figure_page.insert_text((326, 207), "Process", fontsize=9)
    figure_page.insert_text((244, 329), "Output", fontsize=9)
    figure_page.insert_text((72, 402), "The paragraph after the diagram should remain normal text.", fontsize=10)

    doc.set_metadata({"title": "Dense Handbook", "author": "QA"})
    doc.set_toc([[1, "Overview", 1], [1, "Planning", 2], [1, "Appendix", 3]])
    doc.save(str(path))
    doc.close()


def _build_two_column_handbook(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=760)
    page.insert_text((72, 60), "Two Column Handbook", fontsize=16)
    for index, text in enumerate(["Left one", "Left two", "Left three", "Left four"]):
        page.insert_text((72, 112 + index * 34), text, fontsize=10)
    for index, text in enumerate(["Right one", "Right two", "Right three", "Right four"]):
        page.insert_text((340, 112 + index * 34), text, fontsize=10)
    page.insert_text((72, 320), "Conclusion", fontsize=14)
    page.insert_text((72, 350), "The final paragraph spans the normal reading flow.", fontsize=10)
    doc.set_metadata({"title": "Two Column Handbook", "author": "QA"})
    doc.save(str(path))
    doc.close()


if __name__ == "__main__":
    unittest.main()
