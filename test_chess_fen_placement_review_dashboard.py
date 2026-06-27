from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.build_chess_fen_placement_review_dashboard import build_chess_fen_placement_review_dashboard


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ChessFenPlacementReviewDashboardTests(unittest.TestCase):
    def test_dashboard_renders_crop_grid_diff_and_blocker_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "assets" / "diagrams" / "board.png"
            crop.parent.mkdir(parents=True)
            Image.new("RGB", (80, 80), "white").save(crop)
            predicted = "4k3/8/8/8/8/8/8/4K3"
            expected = "4k3/8/8/8/8/8/8/3K4"
            _write_json(
                root / "fen" / "fen_candidates.json",
                {
                    "items": [
                        {
                            "id": "diagram-1",
                            "page": 1,
                            "status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                            "runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                            "placement_runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                            "source_image_path": "assets/diagrams/board.png",
                            "selected_placement": predicted,
                            "acceptance_blockers": [
                                {
                                    "code": "full_fen_metadata_not_accepted",
                                    "category": "full_fen_validation",
                                    "message": "metadata missing",
                                }
                            ],
                            "next_action": "resolve_full_fen_metadata_or_human_verify",
                            "side_to_move": "w",
                            "side_marker_symbol": "\u25b3",
                            "side_marker_status": "trusted_marker",
                            "side_marker_confidence": 0.94,
                            "fen_suppressed_reason": "",
                        }
                    ]
                },
            )
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps({"id": "diagram-1", "fen": f"{expected} w - - 0 1", "crop_path": str(crop)}) + "\n",
                encoding="utf-8",
            )

            result = build_chess_fen_placement_review_dashboard(root, expected_labels=labels)
            html = Path(result["html"]).read_text(encoding="utf-8")
            payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(payload["summary"]["item_count"], 1)
        self.assertEqual(payload["summary"]["placement_machine_accepted"], 1)
        self.assertEqual(payload["items"][0]["square_diff_count"], 2)
        self.assertIn("Chess FEN Two-Crop Review", html)
        self.assertIn("assets/diagrams/board.png", html)
        self.assertIn("Board crop", html)
        self.assertIn("Marker crop", html)
        self.assertIn("Debug overlay", html)
        self.assertIn('data-filter="marker-missing"', html)
        self.assertIn('data-filter="placement-review"', html)
        self.assertIn("full_fen_validation/full_fen_metadata_not_accepted", html)
        self.assertIn("trusted_marker", html)
        self.assertEqual(payload["items"][0]["placement_runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
        self.assertEqual(payload["items"][0]["full_fen_runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertEqual(payload["items"][0]["side_marker_symbol"], "\u25b3")
        self.assertIn("d1", html)
        self.assertNotIn(str(crop), html)

    def test_dashboard_handles_missing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_chess_fen_placement_review_dashboard(Path(temp_dir))
            payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(payload["summary"]["item_count"], 0)


if __name__ == "__main__":
    unittest.main()
