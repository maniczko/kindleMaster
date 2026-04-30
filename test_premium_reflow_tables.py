import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz

from converter import ConversionConfig
from premium_reflow import (
    PublicationTable,
    _classify_pdf_table_rows,
    _merge_continued_tables,
    _publication_table_to_html,
    _table_summary_payload,
    extract_book_premium,
    pdfplumber,
)


class PremiumReflowTableTests(unittest.TestCase):
    def test_publication_table_renders_header_numeric_cells_and_links(self) -> None:
        table = PublicationTable(
            page_index=0,
            bbox=(72.0, 120.0, 420.0, 210.0),
            y_position=120.0,
            rows=[
                ["Metric", "Value", "Source"],
                ["Revenue", "123 PLN", "https://example.com/report"],
                ["Margin", "12%", "www.example.com/margin"],
            ],
            header_rows=1,
            caption="Table 1: Revenue metrics",
        )

        html = _publication_table_to_html(table)

        self.assertIn('<table class="report-table"', html)
        self.assertIn("<caption>Table 1: Revenue metrics</caption>", html)
        self.assertIn('<th scope="col">Metric</th>', html)
        self.assertIn('<td class="numeric-cell">123 PLN</td>', html)
        self.assertIn('<td class="numeric-cell">12%</td>', html)
        self.assertIn('href="https://example.com/report"', html)
        self.assertIn('href="https://www.example.com/margin"', html)

    def test_table_classifier_skips_reference_toc_and_layout_grid_false_positives(self) -> None:
        reference_rows = [["ID", "Zrodlo", "Adres"], ["[R1]", "Visa", "https://example.com"]]
        toc_rows = [["Chapter", "Page"], ["Introduction", "1"], ["Methods", "2"], ["Results", "3"], ["Appendix", "4"]]
        sparse_rows = [["", "", ""], ["", "X", ""], ["", "", ""]]

        self.assertEqual(_classify_pdf_table_rows(reference_rows)[0], "reference_like")
        self.assertEqual(_classify_pdf_table_rows(toc_rows)[0], "toc_like")
        self.assertEqual(_classify_pdf_table_rows(sparse_rows)[0], "layout_grid")

    def test_table_classifier_flags_wide_and_low_confidence_tables(self) -> None:
        wide_rows = [
            ["A", "B", "C", "D", "E", "F"],
            ["1", "2", "3", "4", "5", "6"],
        ]
        low_rows = [["Only one cell"]]

        wide_class, wide_issues, wide_confidence = _classify_pdf_table_rows(wide_rows)
        low_class, low_issues, low_confidence = _classify_pdf_table_rows(low_rows)

        self.assertEqual(wide_class, "wide")
        self.assertIn("wide-table", wide_issues)
        self.assertGreater(wide_confidence, 0.9)
        self.assertEqual(low_class, "low_confidence")
        self.assertIn("low-confidence-table-shape", low_issues)
        self.assertLess(low_confidence, 0.75)

    def test_table_classifier_flags_single_row_fragments_for_review(self) -> None:
        fragment_rows = [
            ["Kategoria", "Wymaganie", "Metryka"],
            ["", "obsługa wyjątków i sporów", "czas reakcji"],
        ]

        classification, issues, confidence = _classify_pdf_table_rows(fragment_rows)

        self.assertEqual(classification, "fragment")
        self.assertIn("table-fragment", issues)
        self.assertLess(confidence, 0.75)

    def test_continued_tables_merge_repeated_headers_on_consecutive_pages(self) -> None:
        first = PublicationTable(
            page_index=0,
            bbox=(72.0, 120.0, 420.0, 760.0),
            y_position=120.0,
            rows=[["Metric", "Value"], ["Revenue", "100"]],
            header_rows=1,
            page_span=[0],
        )
        second = PublicationTable(
            page_index=1,
            bbox=(73.0, 80.0, 421.0, 220.0),
            y_position=80.0,
            rows=[["Metric", "Value"], ["Margin", "12%"]],
            header_rows=1,
            page_span=[1],
        )

        merged = _merge_continued_tables({0: [first], 1: [second]})
        tables = [table for page_tables in merged.values() for table in page_tables]

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].classification, "multi_page")
        self.assertEqual(tables[0].page_span, [0, 1])
        self.assertEqual(tables[0].rows, [["Metric", "Value"], ["Revenue", "100"], ["Margin", "12%"]])
        self.assertIn("multi-page-table", tables[0].issues)

    @unittest.skipIf(pdfplumber is None, "pdfplumber is required for PDF table extraction")
    def test_extract_book_premium_preserves_pdf_table_as_xhtml_and_reports_metrics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "table-report.pdf"
            _build_vector_table_pdf(pdf_path)

            content = extract_book_premium(
                str(pdf_path),
                config=ConversionConfig(language="en"),
                pdf_metadata={"title": "Table Report", "author": "QA"},
            )

        html = "\n".join(part for chapter in content["chapters"] for part in chapter.get("html_parts", []))
        metadata = content.get("metadata") or {}
        table_summary = metadata.get("table_summary") or {}

        self.assertIn("<table", html)
        self.assertIn('class="report-table"', html)
        self.assertIn("<caption>Table 1: Revenue metrics</caption>", html)
        self.assertIn('<th scope="col">Metric</th>', html)
        self.assertEqual(metadata["source_table_count"], 1)
        self.assertEqual(metadata["xhtml_table_count"], 1)
        self.assertEqual(table_summary["table_cell_count"], 9)
        self.assertEqual(table_summary["table_row_count"], 3)
        self.assertEqual(table_summary["table_cell_coverage"], 1.0)
        self.assertEqual(table_summary["wide_table_count"], 0)
        self.assertEqual(table_summary["fragment_table_count"], 0)


def _build_vector_table_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 50), "Table Report", fontsize=18)
    page.insert_text((72, 102), "Table 1: Revenue metrics", fontsize=11)
    x0, y0 = 72, 120
    col_width = 120
    row_height = 30
    rows = [
        ["Metric", "Value", "Source"],
        ["Revenue", "123 PLN", "https://example.com/report"],
        ["Margin", "12%", "OK"],
    ]
    for row_index in range(len(rows) + 1):
        y = y0 + row_index * row_height
        page.draw_line((x0, y), (x0 + len(rows[0]) * col_width, y), color=(0, 0, 0), width=1)
    for column_index in range(len(rows[0]) + 1):
        x = x0 + column_index * col_width
        page.draw_line((x, y0), (x, y0 + len(rows) * row_height), color=(0, 0, 0), width=1)
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            page.insert_text((x0 + column_index * col_width + 5, y0 + row_index * row_height + 20), value, fontsize=9)
    page.insert_text((72, 250), "The following paragraph remains normal body text.", fontsize=11)
    doc.save(str(path))
    doc.close()


if __name__ == "__main__":
    unittest.main()
