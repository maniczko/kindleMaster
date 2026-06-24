from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.export_chess_fen_square_debug_artifacts import export_square_debug_artifacts


def _synthetic_board(path: Path) -> None:
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    for row in range(8):
        for col in range(8):
            color = (230, 230, 230) if (row + col) % 2 == 0 else (70, 70, 70)
            draw.rectangle((col * 20, row * 20, (col + 1) * 20, (row + 1) * 20), fill=color)
    image.save(path)


def _square_rows() -> list[dict]:
    rows: list[dict] = []
    for row in range(8):
        for col in range(8):
            square = f"{chr(ord('a') + col)}{8 - row}"
            rows.append(
                {
                    "square": square,
                    "piece": "K" if square == "e1" else "",
                    "confidence": 0.99,
                    "alternatives": [
                        {"piece": "K" if square == "e1" else "", "confidence": 0.99},
                        {"piece": "Q", "confidence": 0.01},
                        {"piece": "R", "confidence": 0.005},
                        {"piece": "B", "confidence": 0.001},
                    ],
                }
            )
    return rows


class ChessFenSquareDebugArtifactsTests(unittest.TestCase):
    def test_exporter_writes_64_square_crops_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "board.png"
            _synthetic_board(crop)

            payload = export_square_debug_artifacts(crop, _square_rows(), root / "debug", case_id="case-1", top_n=2)

            jsonl_rows = [
                json.loads(line)
                for line in Path(payload["squares_jsonl"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["square_count"], 64)
            self.assertEqual(len(jsonl_rows), 64)
            self.assertTrue(Path(jsonl_rows[0]["square_crop_path"]).is_file())
            e1 = next(row for row in jsonl_rows if row["square"] == "e1")
            self.assertEqual(e1["piece"], "K")
            self.assertEqual(len(e1["alternatives"]), 2)

    def test_exporter_reports_missing_crop_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = export_square_debug_artifacts(root / "missing.png", _square_rows(), root / "debug")

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["square_count"], 0)
        self.assertEqual(payload["issues"][0]["code"], "crop_path_missing_on_disk")


if __name__ == "__main__":
    unittest.main()
