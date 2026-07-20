from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from chess_position_recognizer import _clear_piece_template_cache, _load_piece_templates_with_cache_info
from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus
from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer


def _write_png(path: Path, *, color: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (16, 16), color)
    image.save(path)


def _png_bytes(*, color: int = 255) -> bytes:
    output = io.BytesIO()
    Image.new("L", (32, 32), color).save(output, format="PNG")
    return output.getvalue()


class ChessFenEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_piece_template_cache()

    def tearDown(self) -> None:
        _clear_piece_template_cache()

    def test_loaded_template_cache_hits_and_invalidates_when_directory_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            template_dir = Path(temp_dir) / "templates"
            _write_png(template_dir / "K_white.png", color=0)

            first_templates, first_info = _load_piece_templates_with_cache_info(template_dir)
            second_templates, second_info = _load_piece_templates_with_cache_info(template_dir)

            self.assertFalse(first_info["cache_hit"])
            self.assertTrue(second_info["cache_hit"])
            self.assertIs(first_templates, second_templates)

            _write_png(template_dir / "Q_white.png", color=64)

            third_templates, third_info = _load_piece_templates_with_cache_info(template_dir)

            self.assertFalse(third_info["cache_hit"])
            self.assertIn("Q", third_templates)
            self.assertEqual(third_info["template_file_count"], 2)

    def test_evaluate_chess_fen_recognizer_reports_case_and_summary_timings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            template_dir = root / "templates"
            crop_path = root / "crop.png"
            labels_path = root / "labels.jsonl"
            _write_png(template_dir / "K_template.png", color=0)
            crop_path.write_bytes(_png_bytes())
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "case-1",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "crop_path": str(crop_path),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            fake_result = {
                "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "full_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "placement": "4k3/8/8/8/8/8/8/4K3",
                "confidence": 0.99,
                "warnings": [],
                "requires_review": False,
                "squares": [],
            }

            with mock.patch(
                "scripts.evaluate_chess_fen_recognizer.recognize_chess_position_from_image",
                return_value=mock.Mock(to_dict=mock.Mock(return_value=fake_result)),
            ):
                first = evaluate_chess_fen_recognizer(labels_path, template_dir=template_dir)
                second = evaluate_chess_fen_recognizer(labels_path, template_dir=template_dir)

        self.assertEqual(first["status"], "passed")
        self.assertFalse(first["template_cache"]["cache_hit"])
        self.assertTrue(second["template_cache"]["cache_hit"])
        self.assertIn("timing_breakdown", first)
        self.assertIn("template_load", first["timing_breakdown"])
        self.assertIn("recognition", first["timing_breakdown"])
        self.assertIn("total", first["timing_breakdown"])
        self.assertEqual(len(first["cases"]), 1)
        self.assertIn("timing_breakdown", first["cases"][0])
        self.assertIn("recognize", first["cases"][0]["timing_breakdown"])
        self.assertIn("diagnostics", first["cases"][0]["timing_breakdown"])

    def test_evaluate_chess_fen_corpus_propagates_timing_breakdown_and_cache_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            labels_path = root / "labels.jsonl"
            manifest_path.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "fen-profile",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "language": "pl",
                                "chess_fen_seed_labels": str(labels_path),
                                "chess_fen_template_profile": "fundamenty_merida_like",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch(
                "scripts.evaluate_chess_fen_corpus.validate_chess_fen_labels",
                return_value={
                    "status": "passed",
                    "label_count": 20,
                    "valid_label_count": 20,
                    "issue_count": 0,
                },
            ), mock.patch(
                "scripts.evaluate_chess_fen_corpus.evaluate_chess_fen_recognizer",
                return_value={
                    "status": "passed",
                    "case_count": 20,
                    "fen_count": 20,
                    "exact_fen_count": 20,
                    "exact_fen_accuracy": 1.0,
                    "false_positive_count": 0,
                    "false_positive_rate": 0.0,
                    "square_accuracy": 1.0,
                    "template_cache": {
                        "cache_hit": True,
                        "template_dir": "cached-profile",
                        "template_file_count": 14,
                        "template_variant_count": 14,
                        "template_label_count": 7,
                    },
                    "timing_breakdown": {
                        "labels_load": 0.01,
                        "template_load": 0.02,
                        "crop_read": 0.03,
                        "recognition": 0.04,
                        "square_debug": 0.0,
                        "diagnostics": 0.01,
                        "total": 0.11,
                    },
                },
            ):
                payload = evaluate_chess_fen_corpus(manifest_path, min_profile_count=1)

        self.assertEqual(payload["status"], "passed")
        self.assertIn("timing_breakdown", payload)
        self.assertIn("manifest_load", payload["timing_breakdown"])
        self.assertIn("recognizer_evaluation", payload["timing_breakdown"])
        self.assertEqual(len(payload["cases"]), 1)
        self.assertEqual(payload["cases"][0]["template_cache"]["cache_hit"], True)
        self.assertEqual(payload["cases"][0]["recognizer_timing_breakdown"]["template_load"], 0.02)
        self.assertIn("recognizer_evaluation", payload["cases"][0]["timing_breakdown"])


if __name__ == "__main__":
    unittest.main()
