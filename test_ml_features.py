from __future__ import annotations

import unittest
from types import SimpleNamespace

from ml_features import docx_route_feature_payload, route_feature_payload, route_features_hash


class MlFeaturesTests(unittest.TestCase):
    def test_pdf_feature_payload_is_stable_and_ratio_based(self) -> None:
        analysis = SimpleNamespace(
            page_count=10,
            text_pages=8,
            scanned_pages=1,
            image_pages=3,
            has_toc=True,
            has_tables=False,
            has_diagrams=True,
            has_meaningful_images=True,
            estimated_columns=2,
            heading_density=0.25,
            font_consistency=0.8,
            layout_heavy=True,
            text_heavy=False,
        )

        payload = route_feature_payload(analysis, input_type="pdf")

        self.assertEqual(payload["input_type"], "pdf")
        self.assertEqual(payload["page_count"], 10)
        self.assertEqual(payload["text_page_ratio"], 0.8)
        self.assertEqual(payload["scanned_page_ratio"], 0.1)
        self.assertEqual(payload["image_page_ratio"], 0.3)
        self.assertTrue(payload["has_diagrams"])
        self.assertEqual(route_features_hash(payload), route_features_hash(dict(reversed(payload.items()))))

    def test_docx_feature_payload_preserves_counts(self) -> None:
        payload = docx_route_feature_payload(
            {
                "paragraph_count": 12,
                "heading1_count": 2,
                "heading2_count": 3,
                "heading3_count": 1,
                "list_count": 4,
                "table_count": 1,
                "image_count": 2,
                "hyperlink_count": 5,
                "publication_analysis": {
                    "has_toc": True,
                    "has_tables": True,
                    "has_diagrams": False,
                    "has_meaningful_images": True,
                    "layout_heavy": False,
                    "text_heavy": True,
                    "estimated_columns": 1,
                    "heading_density": 1.0,
                    "font_consistency": 1.0,
                },
            }
        )

        self.assertEqual(payload["input_type"], "docx")
        self.assertEqual(payload["docx_paragraph_count"], 12)
        self.assertEqual(payload["docx_heading1_count"], 2)
        self.assertEqual(payload["docx_hyperlink_count"], 5)
        self.assertTrue(payload["text_heavy"])


if __name__ == "__main__":
    unittest.main()
