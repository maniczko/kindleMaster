from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from chess_crop_qa_benchmark import evaluate_crop_qa_benchmark
from chess_diagram_fingerprint import (
    build_diagram_fingerprint,
    diagram_perceptual_hash,
    measure_expected_diagram_recall,
    preferred_record_key,
)
from chess_learning_labels import normalize_chess_learning_label


SOURCE_SHA = "a" * 64


def _checkerboard(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    square = size / 8
    for rank in range(8):
        for file_index in range(8):
            if (rank + file_index) % 2:
                draw.rectangle(
                    (
                        round(file_index * square),
                        round(rank * square),
                        round((file_index + 1) * square),
                        round((rank + 1) * square),
                    ),
                    fill="#4a4a4a",
                )
    return image


class ChessDiagramFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_across_render_scale_and_tiny_bbox_drift(self) -> None:
        board = _checkerboard(256)
        scaled = board.resize((512, 512), Image.Resampling.NEAREST)

        first = build_diagram_fingerprint(
            source_sha256=SOURCE_SHA,
            page=7,
            normalized_bbox_xyxy=[0.100, 0.200, 0.900, 0.800],
            board_crop=board,
        )
        second = build_diagram_fingerprint(
            source_sha256=SOURCE_SHA,
            page=7,
            normalized_bbox_xyxy=[0.101, 0.199, 0.899, 0.801],
            board_crop=scaled,
        )

        self.assertEqual(diagram_perceptual_hash(board), diagram_perceptual_hash(scaled))
        self.assertEqual(first["diagram_fingerprint"], second["diagram_fingerprint"])
        self.assertEqual(first["bbox_quantized"], second["bbox_quantized"])
        self.assertNotEqual(
            first["diagram_fingerprint"],
            build_diagram_fingerprint(
                source_sha256=SOURCE_SHA,
                page=8,
                normalized_bbox_xyxy=[0.100, 0.200, 0.900, 0.800],
                board_crop=board,
            )["diagram_fingerprint"],
        )

    def test_expected_recall_prefers_fingerprint_then_normalized_bbox(self) -> None:
        detected = [
            {
                "diagram_id": "new-a",
                "diagram_fingerprint": "dfp_exact",
                "page": 3,
                "normalized_bbox_xyxy": [0.1, 0.1, 0.4, 0.4],
            },
            {
                "diagram_id": "new-b",
                "diagram_fingerprint": "dfp_runtime_b",
                "page": 3,
                "normalized_bbox_xyxy": [0.55, 0.1, 0.9, 0.45],
            },
        ]
        expected = {
            "source_document_sha256": SOURCE_SHA,
            "diagrams": [
                {
                    "diagram_id": "old-a",
                    "diagram_fingerprint": "dfp_exact",
                    "page": 3,
                    "normalized_bbox_xyxy": [0.1, 0.1, 0.4, 0.4],
                },
                {
                    "diagram_id": "old-b",
                    "page": 3,
                    "normalized_bbox_xyxy": [0.56, 0.11, 0.89, 0.44],
                },
            ],
        }

        report = measure_expected_diagram_recall(detected, expected, source_sha256=SOURCE_SHA)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["expected_diagram_recall"], 1.0)
        self.assertEqual(
            [match["match_method"] for match in report["matches"]],
            ["fingerprint", "page_normalized_bbox"],
        )
        self.assertEqual(preferred_record_key(detected[0]), ("diagram_fingerprint", "dfp_exact"))

    def test_qa_and_learning_labels_reuse_fingerprint_when_ids_change(self) -> None:
        fingerprint = "dfp_" + "b" * 32
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "diagram_id": "legacy-id",
                        "diagram_fingerprint": fingerprint,
                        "manual_visible_marker": "filled_triangle",
                        "manual_side_to_move": "b",
                        "issue_type": "system_suggestion_mismatch",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            actual_path = root / "actual.json"
            actual_path.write_text(
                json.dumps(
                    {
                        "diagrams": [
                            {
                                "diagram_id": "rerun-id",
                                "diagram_fingerprint": fingerprint,
                                "side_to_move": "b",
                                "side_marker_status": "trusted_marker",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            qa_report = evaluate_crop_qa_benchmark(labels_path, actual_path=actual_path)

        self.assertEqual(qa_report["summary"]["matched_actual_count"], 1)
        self.assertEqual(qa_report["summary"]["missing_actual_count"], 0)
        self.assertEqual(qa_report["matched"][0]["match_method"], "diagram_fingerprint")

        base_label = {
            "diagram_fingerprint": fingerprint,
            "label_type": "fen",
            "label_value": "correct",
            "reviewer": "human-reviewer",
            "created_at": "2026-07-10T10:00:00Z",
            "verification_source": "human_visual",
            "human_verified": True,
            "board_crop_hash": "c" * 64,
        }
        first, first_issues = normalize_chess_learning_label({**base_label, "diagram_id": "legacy-id"})
        second, second_issues = normalize_chess_learning_label({**base_label, "diagram_id": "rerun-id"})
        self.assertEqual(first_issues, [])
        self.assertEqual(second_issues, [])
        self.assertEqual(first["label_id"], second["label_id"])


if __name__ == "__main__":
    unittest.main()
