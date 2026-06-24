from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from chess_position_recognizer import (
    board_crop_grid_diagnostics_from_image,
    classify_board_crop_problem,
    render_board_grid_overlay,
)
from scripts.audit_chess_pipeline_breakdown import audit_chess_pipeline_breakdown


class ChessFenCropGridDiagnosticsTests(unittest.TestCase):
    def test_board_crop_grid_diagnostics_returns_normalization_and_taxonomy(self):
        image_data = _checkerboard_png_bytes()

        diagnostics = board_crop_grid_diagnostics_from_image(image_data)

        self.assertIn("normalization_variant", diagnostics)
        self.assertIn("original_size", diagnostics)
        self.assertIn("normalized_size", diagnostics)
        self.assertIn("board_signal", diagnostics)
        self.assertIn("grid_confidence", diagnostics)
        self.assertIn("crop_problem_taxonomy", diagnostics)
        self.assertIn(diagnostics["crop_problem_taxonomy"], {
            "clean_board",
            "caption_included",
            "coordinates_included",
            "thick_border",
            "partial_board",
            "shifted_grid",
            "multi_board_region",
            "unknown",
        })

    def test_render_board_grid_overlay_writes_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "overlay.png"

            result = render_board_grid_overlay(_checkerboard_png_bytes(), output)

            self.assertEqual(result["status"], "written")
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

    def test_captioned_board_taxonomy_is_not_runtime_acceptance(self):
        taxonomy = classify_board_crop_problem(_checkerboard_png_bytes(caption=True))

        self.assertIn(taxonomy, {"caption_included", "shifted_grid", "unknown", "clean_board"})

    def test_audit_harness_writes_fen_overlay_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_with_fen_case(root)
            output = root / "reports"

            audit_chess_pipeline_breakdown(manifest, output_dir=output)
            summary = json.loads((output / "audit_summary.json").read_text(encoding="utf-8"))
            cases = [
                json.loads(line)
                for line in (output / "audit_cases.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(len(cases), 1)
            self.assertIn("crop_problem_taxonomy", cases[0])
            self.assertIn("normalization_variant", cases[0])
            self.assertTrue(cases[0]["overlay_path"])
            self.assertTrue(Path(cases[0]["overlay_path"]).exists())
            self.assertEqual(summary["fen"]["case_count"], 1)
            self.assertEqual(summary["fen"]["crop_correct_known_count"], 1)
            self.assertEqual(summary["fen"]["grid_measured_count"], 1)
            self.assertIn("grid_confidence_average", summary["fen"])
            self.assertIn("crop_problem_counts", summary["fen"])


def _checkerboard_png_bytes(*, caption: bool = False) -> bytes:
    cell = 20
    board_size = cell * 8
    height = board_size + (36 if caption else 0)
    image = Image.new("L", (board_size, height), 240)
    draw = ImageDraw.Draw(image)
    for row in range(8):
        for col in range(8):
            fill = 210 if (row + col) % 2 == 0 else 90
            draw.rectangle((col * cell, row * cell, (col + 1) * cell - 1, (row + 1) * cell - 1), fill=fill)
    if caption:
        draw.text((8, board_size + 8), "White to move", fill=20)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_dataset_with_fen_case(root: Path) -> Path:
    (root / "labels").mkdir(parents=True)
    (root / "crops").mkdir()
    (root / "overlays").mkdir()
    (root / "crops" / "board.png").write_bytes(_checkerboard_png_bytes())
    (root / "labels" / "fen_ground_truth.jsonl").write_text(
        json.dumps(
            {
                "id": "fen-overlay",
                "source_pdf": "book.pdf",
                "page": 1,
                "crop_path": "crops/board.png",
                "expected_placement": "8/8/8/8/8/8/4K3/4k3",
                "expected_full_fen": "",
                "side_to_move_source": "unknown",
                "crop_expected_bbox": [0, 0, 160, 160],
                "crop_correct": True,
                "crop_has_caption": False,
                "crop_has_coordinates": False,
                "human_verified": True,
                "verified_by": "reviewer",
                "verified_at": "2026-06-20",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for name in ("pgn_ground_truth.jsonl", "negative_samples.jsonl"):
        (root / "labels" / name).write_text("", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "kindlemaster.chess_audit_dataset.v1",
                "fen_ground_truth": "labels/fen_ground_truth.jsonl",
                "pgn_ground_truth": "labels/pgn_ground_truth.jsonl",
                "negative_samples": "labels/negative_samples.jsonl",
                "crops_dir": "crops",
                "overlays_dir": "overlays",
            }
        ),
        encoding="utf-8",
    )
    return manifest


if __name__ == "__main__":
    unittest.main()
