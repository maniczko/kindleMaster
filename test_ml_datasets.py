from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.build_ml_datasets import build_ml_datasets


class MlDatasetBuilderTests(unittest.TestCase):
    def test_builder_emits_route_jsonl_and_explicit_skip_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "inputs").mkdir()
            pdf_path = root / "inputs" / "book.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            manifest = {
                "cases": [
                    {
                        "id": "book_case",
                        "input_type": "pdf",
                        "document_class": "book",
                        "language": "en",
                        "target_path": "inputs/book.pdf",
                    },
                    {"id": "missing_docx", "input_type": "docx", "target_path": "inputs/missing.docx"},
                    {"id": "epub_case", "input_type": "epub", "target_path": "inputs/book.epub"},
                ]
            }
            labels = {
                "cases": {
                    "book_case": {"route_label": "book_reflow"},
                    "missing_docx": {"route_label": "docx_reflow"},
                }
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps(labels), encoding="utf-8")

            payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                repo_root=root,
                pdf_analyzer=lambda _path: SimpleNamespace(
                    profile="book_reflow",
                    confidence=0.91,
                    page_count=4,
                    text_pages=4,
                    scanned_pages=0,
                    image_pages=0,
                    has_toc=True,
                    has_tables=False,
                    has_diagrams=False,
                    has_meaningful_images=False,
                    estimated_columns=1,
                    heading_density=0.4,
                    font_consistency=0.9,
                    layout_heavy=False,
                    text_heavy=True,
                ),
            )

            route_lines = (root / "reports" / "ml" / "datasets" / "route_examples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()

            self.assertEqual(payload["route_example_count"], 1)
            self.assertEqual(len(route_lines), 1)
            self.assertEqual(json.loads(route_lines[0])["label"], "book_reflow")
            self.assertEqual(payload["status"], "insufficient_data")
            self.assertIn("missing_input", {item["reason"] for item in payload["skipped"]})
            self.assertIn("unsupported_input_type:epub", {item["reason"] for item in payload["skipped"]})


if __name__ == "__main__":
    unittest.main()
