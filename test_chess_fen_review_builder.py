from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from chess_fen_review_builder import FenReviewBuildError, build_conversion_fen_review


class ChessFenReviewBuilderTests(unittest.TestCase):
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def _record(self) -> dict:
        squares = [{"square": f"s{index}", "piece": "", "confidence": 0.99} for index in range(64)]
        squares[4]["piece"] = "k"
        squares[60]["piece"] = "K"
        return {
            "id": "layout-chess-p010-d01",
            "page_number": 10,
            "source_order": 1,
            "caption": "Strona 10, diagram 1",
            "bbox": [1, 2, 101, 102],
            "board_crop_path": "review/chess_fen/two_crop/diagram_board.png",
            "side_marker_crop_path": "review/chess_fen/two_crop/diagram_marker.png",
            "side_marker_search_crop_path": "review/chess_fen/two_crop/diagram_marker_search.png",
            "debug_overlay_path": "review/chess_fen/two_crop/diagram_overlay.png",
            "side_to_move": "b",
            "side_marker_symbol": "black-triangle",
            "side_marker_status": "trusted_marker",
            "board_crop_quality": "pass",
            "marker_crop_quality": "pass",
            "reason": "fen_not_recognized",
            "recognition_blockers": ["model_template_conflict"],
            "model_runtime": {
                "validation_fen": "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
                "placement": "4k3/8/8/8/8/8/8/4K3",
                "confidence": 0.995,
                "blockers": ["board_confidence_below_calibrated_threshold"],
                "owning_blocker": "model_template_conflict",
                "squares": squares,
            },
        }

    def test_builds_source_bound_piece_grid_review_from_conversion_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifact-1"
            report_dir = root / "report"
            crop_dir = root / "review" / "chess_fen" / "two_crop"
            input_path = root / "input" / "study.pdf"
            report_dir.mkdir(parents=True)
            crop_dir.mkdir(parents=True)
            input_path.parent.mkdir(parents=True)
            input_path.write_bytes(b"%PDF-1.4\nsource")
            for name in ("diagram_board.png", "diagram_marker.png", "diagram_marker_search.png", "diagram_overlay.png"):
                (crop_dir / name).write_bytes(self.PNG)
            diagrams_path = report_dir / "chess_diagrams.json"
            diagrams_path.write_text(
                json.dumps({"diagram_count": 1, "records": [self._record()]}),
                encoding="utf-8",
            )

            result = build_conversion_fen_review(
                artifact_id="artifact-1",
                diagrams_path=diagrams_path,
            )

            draft_path = root / "review" / "fen_manual_draft.jsonl"
            row = json.loads(draft_path.read_text(encoding="utf-8").strip())
            self.assertEqual(result["diagram_count"], 1)
            self.assertEqual(row["review_contract"], "source_bound_piece_grid_v2")
            self.assertEqual(len(row["square_labels"]), 64)
            self.assertEqual(row["square_labels"][4], "k")
            self.assertEqual(row["square_labels"][60], "K")
            self.assertEqual(row["manual_side_to_move"], "")
            self.assertEqual(row["side_to_move"], "b")
            self.assertFalse(row["piece_labels_verified"])
            self.assertTrue((root / "review" / row["board_crop_rel_path"]).is_file())
            self.assertTrue((root / "review" / row["context_crop_rel_path"]).is_file())
            self.assertTrue((root / "review" / row["marker_crop_rel_path"]).is_file())
            self.assertTrue((root / "review" / row["marker_search_crop_rel_path"]).is_file())
            html = (root / "review" / "fen_manual_review.html").read_text(encoding="utf-8")
            self.assertIn("layout-chess-p010-d01", html)
            self.assertIn("fen_manual_assets/", html)

    def test_missing_board_blocks_partial_review_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifact-1"
            report_dir = root / "report"
            report_dir.mkdir(parents=True)
            diagrams_path = report_dir / "chess_diagrams.json"
            diagrams_path.write_text(
                json.dumps({"diagram_count": 1, "records": [self._record()]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(FenReviewBuildError, "missing board assets"):
                build_conversion_fen_review(
                    artifact_id="artifact-1",
                    diagrams_path=diagrams_path,
                )

            self.assertFalse((root / "review" / "fen_manual_review.html").exists())


if __name__ == "__main__":
    unittest.main()
