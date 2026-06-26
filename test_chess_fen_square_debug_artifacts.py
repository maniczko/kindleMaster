from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.build_chess_fen_square_debug_manifest import build_square_debug_review_manifest
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
            self.assertTrue(Path(payload["grid_overlay_path"]).is_file())
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

    def test_review_manifest_writes_all_cases_with_explicit_unavailable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_root = root / "crops"
            crop_root.mkdir()
            crop = crop_root / "book_p014_runtime_02.png"
            _synthetic_board(crop)
            report = root / "diagnostics.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "kindlemaster.chess_fen.review_blockers.v1",
                        "items": [
                            {
                                "diagram_id": "p14:scan_chess_p014_02.png",
                                "page": 14,
                                "status": "requires_review",
                                "runtime_status": "image-template-board",
                                "selected_value": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                                "selected_placement": "8/8/8/8/8/8/4K3/4k3",
                                "ai_consensus_fen": "8/8/8/8/8/8/3K4/4k3 w - - 0 1",
                                "warnings": ["piece_template_confidence_below_threshold"],
                                "all_blockers": ["piece_template_confidence_below_threshold"],
                                "confidence": 0.829,
                            },
                            {
                                "diagram_id": "p15:scan_chess_p015_99.png",
                                "page": 15,
                                "status": "requires_review",
                                "selected_value": "8/8/8/8/8/8/4K3/4k3 b - - 0 1",
                                "selected_placement": "8/8/8/8/8/8/4K3/4k3",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = build_square_debug_review_manifest(report, output_dir=root / "debug", crop_roots=[crop_root], top_n=2)

            self.assertEqual(payload["case_count"], 2)
            self.assertEqual(payload["available_crop_count"], 1)
            self.assertEqual(payload["unavailable_crop_count"], 1)
            self.assertEqual(payload["square_entry_count"], 128)
            self.assertEqual(payload["cases"][0]["board_crop"]["status"], "available")
            self.assertEqual(payload["cases"][0]["grid_overlay"]["status"], "available")
            self.assertTrue(Path(payload["cases"][0]["grid_overlay"]["path"]).is_file())
            self.assertEqual(len(payload["cases"][0]["squares"]), 64)
            self.assertEqual(payload["cases"][0]["candidate_diff"]["source"], "candidate_vs_ai")
            self.assertEqual(payload["cases"][0]["strict_output"]["status"], "not_promoted")
            self.assertEqual(payload["cases"][1]["board_crop"]["status"], "unavailable")
            self.assertTrue(all(square["square_crop"]["status"] == "unavailable" for square in payload["cases"][1]["squares"]))
            manifest = json.loads((root / "debug" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "kindlemaster.chess_fen.square_debug_review_manifest.v1")

    def test_review_manifest_uses_unavailable_markers_for_missing_per_square_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "crop.png"
            _synthetic_board(crop)
            report = root / "diagnostics.json"
            report.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "diagram_id": "case",
                                "crop_path": str(crop),
                                "selected_placement": "8/8/8/8/8/8/4K3/4k3",
                                "selected_value": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = build_square_debug_review_manifest(report, output_dir=root / "debug")

            squares = payload["cases"][0]["squares"]
            self.assertEqual(len(squares), 64)
            self.assertTrue(all(square["alternatives"][0]["status"] == "unavailable" for square in squares))
            self.assertTrue(all(square["square_crop"]["status"] == "available" for square in squares))


if __name__ == "__main__":
    unittest.main()
