from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from PIL import Image


CORPUS_ROOT = Path("reference_inputs/chess_fen/marker_crops")
REQUIRED_CLASSES = {
    "white_outline_triangle",
    "black_filled_triangle",
    "bad_crop",
    "multiple",
    "unclear",
}


class ChessMarkerCropCorpusTests(unittest.TestCase):
    def test_manifest_is_complete_and_policy_safe(self) -> None:
        manifest_path = CORPUS_ROOT / "manifest.json"
        self.assertTrue(manifest_path.is_file(), "marker crop corpus manifest is missing")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("schema"), "kindlemaster.chess_fen.marker_crop_corpus.v1")
        self.assertEqual(manifest.get("policy"), "no_full_page_content_minimal_marker_crops_only")
        self.assertFalse((manifest.get("source_policy") or {}).get("real_book_crops_committed"))
        self.assertTrue((manifest.get("source_policy") or {}).get("local_fixture_pack_supported"))
        self.assertFalse((manifest.get("source_policy") or {}).get("allowed_for_runtime_truth"))

        classes = manifest.get("classes") or {}
        items = manifest.get("items") or []
        self.assertTrue(REQUIRED_CLASSES.issubset(set(classes)))
        self.assertGreaterEqual(len(items), len(REQUIRED_CLASSES))

        by_class = Counter(str(item.get("class") or "") for item in items)
        for class_name in REQUIRED_CLASSES:
            self.assertGreater(by_class[class_name], 0, f"{class_name} must be non-empty")
            self.assertEqual(by_class[class_name], int(classes[class_name]))
            self.assertTrue((CORPUS_ROOT / class_name).is_dir())

        for item in items:
            with self.subTest(item=item.get("id")):
                rel_path = Path(str(item.get("path") or ""))
                self.assertFalse(rel_path.is_absolute())
                self.assertNotIn("..", rel_path.parts)
                path = CORPUS_ROOT / rel_path
                self.assertTrue(path.is_file(), f"missing corpus image: {rel_path}")
                self.assertIn(path.suffix.lower(), {".png", ".webp"})
                self.assertEqual(item.get("source"), "synthetic")
                self.assertTrue(item.get("allowed_for_training"))
                self.assertFalse(item.get("allowed_for_runtime_truth"))
                with Image.open(path) as image:
                    self.assertLessEqual(max(image.size), 96)
                    self.assertGreaterEqual(min(image.size), 24)

    def test_side_labels_are_limited_to_marker_classes(self) -> None:
        manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
        label_by_class = {
            "white_outline_triangle": "w",
            "black_filled_triangle": "b",
            "bad_crop": "",
            "multiple": "",
            "unclear": "",
        }
        symbol_by_class = {
            "white_outline_triangle": "△",
            "black_filled_triangle": "▼",
            "bad_crop": "",
            "multiple": "multiple",
            "unclear": "?",
        }

        for item in manifest.get("items") or []:
            class_name = str(item.get("class") or "")
            with self.subTest(item=item.get("id")):
                self.assertEqual(item.get("label"), label_by_class[class_name])
                self.assertEqual(item.get("symbol"), symbol_by_class[class_name])


if __name__ == "__main__":
    unittest.main()
