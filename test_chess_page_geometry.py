from __future__ import annotations

import unittest

from chess_page_geometry import analyze_exercise_page_geometry, order_geometry_items


CHESS_GLYPHS = "".join(chr(0xF031 + (index % 16)) for index in range(80))


def _woodpecker_six_diagram_blocks(numbers: list[int]) -> list[dict]:
    blocks: list[dict] = []
    diagram_boxes = [
        [37.6, 66.0, 235.6, 255.7],
        [37.6, 258.6, 235.6, 448.3],
        [37.6, 451.2, 235.6, 640.9],
        [261.5, 66.0, 459.5, 255.7],
        [261.5, 258.6, 459.5, 448.3],
        [261.5, 451.2, 459.5, 640.9],
    ]
    for index, (number, diagram_box) in enumerate(zip(numbers, diagram_boxes), start=1):
        number_x = 22.0 if index <= 3 else 246.0
        number_y = diagram_box[1] + 27.0
        blocks.append(
            {
                "type": "text",
                "text": str(number),
                "bbox": [number_x, number_y, number_x + 16.0, number_y + 19.0],
                "parent_bbox": [number_x, number_y, number_x + 16.0, number_y + 19.0],
                "block_index": index,
                "line_index": 0,
            }
        )
        blocks.append(
            {
                "type": "text",
                "text": f"Game {number} {CHESS_GLYPHS}",
                "bbox": diagram_box,
                "parent_bbox": diagram_box,
                "block_index": 100 + index,
                "line_index": 0,
            }
        )
    blocks.append(
        {
            "type": "text",
            "text": "32",
            "bbox": [42.5, 33.8, 52.5, 46.5],
            "parent_bbox": [42.5, 33.8, 52.5, 46.5],
            "block_index": 0,
            "line_index": 0,
        }
    )
    return blocks


class ChessPageGeometryTests(unittest.TestCase):
    def test_six_diagram_page_uses_column_major_printed_order(self) -> None:
        result = analyze_exercise_page_geometry(
            _woodpecker_six_diagram_blocks([1, 2, 3, 4, 5, 6]),
            page_number=33,
            page_width=481.89,
            page_height=680.315,
        )

        self.assertEqual(result["column_count"], 2)
        self.assertEqual(result["diagram_count"], 6)
        self.assertEqual([item["exercise_number"] for item in result["assignments"]], [1, 2, 3, 4, 5, 6])
        self.assertEqual([item["column"] for item in result["assignments"]], [1, 1, 1, 2, 2, 2])
        self.assertEqual(result["status"], "accepted")

    def test_corrupted_printed_sequence_stays_review_only(self) -> None:
        result = analyze_exercise_page_geometry(
            _woodpecker_six_diagram_blocks([625, 262, 267, 628, 629, 630]),
            page_number=138,
            page_width=481.89,
            page_height=680.315,
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertIn("NON_CONTIGUOUS_EXERCISE_SEQUENCE", result["warnings"])
        self.assertTrue(all(item["exercise_number"] is None for item in result["assignments"]))
        self.assertEqual([item["candidate_number"] for item in result["assignments"]], [625, 262, 267, 628, 629, 630])

    def test_duplicate_printed_number_is_not_accepted(self) -> None:
        result = analyze_exercise_page_geometry(
            _woodpecker_six_diagram_blocks([1, 2, 2, 4, 5, 6]),
            page_number=33,
            page_width=481.89,
            page_height=680.315,
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertIn("DUPLICATE_EXERCISE_NUMBER", result["warnings"])
        self.assertTrue(all(item["exercise_number"] is None for item in result["assignments"]))
        self.assertEqual(len({item["diagram_block_index"] for item in result["assignments"]}), 6)

    def test_low_confidence_match_has_explicit_warning_and_coordinates(self) -> None:
        blocks = _woodpecker_six_diagram_blocks([1, 2, 3, 4, 5, 6])
        blocks[0]["bbox"][1] += 55.0
        blocks[0]["bbox"][3] += 55.0
        blocks[0]["parent_bbox"] = list(blocks[0]["bbox"])

        result = analyze_exercise_page_geometry(
            blocks,
            page_number=33,
            page_width=481.89,
            page_height=680.315,
        )

        first = result["assignments"][0]
        self.assertEqual(first["status"], "needs_review")
        self.assertIn("LOW_CONFIDENCE_EXERCISE_NUMBER_MATCH", first["warnings"])
        self.assertEqual(len(first["number_bbox"]), 4)
        self.assertIn("LOW_CONFIDENCE_EXERCISE_NUMBER_MATCH", result["warnings"])

    def test_diagram_order_is_column_major_not_interleaved_y_x(self) -> None:
        diagrams = [
            {"id": "right-1", "bbox": [0.55, 0.10, 0.95, 0.30]},
            {"id": "left-2", "bbox": [0.05, 0.40, 0.45, 0.60]},
            {"id": "right-2", "bbox": [0.55, 0.40, 0.95, 0.60]},
            {"id": "left-1", "bbox": [0.05, 0.10, 0.45, 0.30]},
        ]

        ordered = order_geometry_items(diagrams, bbox_getter=lambda item: item["bbox"], page_width=1.0)

        self.assertEqual([item["id"] for item in ordered], ["left-1", "left-2", "right-1", "right-2"])


if __name__ == "__main__":
    unittest.main()
