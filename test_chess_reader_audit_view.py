from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chess_study_export import SEMANTIC_BOOK_SCHEMA, _render_standalone_html, _semantic_source_index_html


class ChessReaderAuditViewTests(unittest.TestCase):
    def test_audit_view_shows_overlay_crops_reason_codes_and_reader_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _render_standalone_html(
                out,
                structure={"chapters": [{"chapter_no": 1, "title": "Mating motifs"}]},
                positions={
                    "positions": [
                        {
                            "id": "p010_d01",
                            "label": "Diagram 1-1",
                            "diagram_page": 10,
                            "status": "needs_review",
                            "source_crop": "assets/board.png",
                            "board_crop_path": "assets/tight-board.png",
                            "side_marker_crop_path": "assets/marker.png",
                            "debug_overlay_path": "assets/overlay.png",
                            "rendered_diagram": "assets/rendered.png",
                            "side_to_move": "unknown",
                            "board_crop_quality": "fail",
                            "board_crop_fail_reason": "board_bbox_not_tight_8x8",
                            "marker_crop_quality": "fail",
                            "marker_crop_fail_reason": "marker_missing",
                            "fen_status": "needs_review",
                            "pgn_status": "needs_review",
                            "side_to_move_status": "marker_missing",
                            "manual_review_reason": "marker_missing",
                            "board_bbox": [10, 20, 110, 120],
                            "marker_search_zones": {"right": [112, 20, 150, 120]},
                            "selected_marker_zone": "right",
                            "marker_bbox": [122, 32, 134, 44],
                            "bbox": [8, 18, 112, 123],
                            "text_block_bbox": [4, 1, 90, 16],
                        }
                    ]
                },
                qa_report={"summary": {"pages": 1, "diagrams_total": 1}, "problems": []},
                page_model={"pages": [{"page": 10, "page_image": "assets/page-10.png", "elements": []}]},
                notation_fragments={"fragments": []},
            )

            html = (out / "standalone_audit.html").read_text(encoding="utf-8")

        self.assertIn("Audit View / Source Preview", html)
        self.assertIn("Source page overlay", html)
        self.assertIn("Tight board crop", html)
        self.assertIn("Marker crop", html)
        self.assertIn("Semantic reader block preview", html)
        self.assertIn("board_crop_quality", html)
        self.assertIn("board_crop_fail_reason", html)
        self.assertIn("marker_crop_fail_reason", html)
        self.assertIn("marker_search_zones", html)
        self.assertIn("selected_marker_zone", html)
        self.assertIn("marker_bbox", html)
        self.assertIn("manual_review_reason", html)
        self.assertIn("board_bbox_not_tight_8x8", html)
        self.assertIn("marker_missing", html)
        self.assertIn('href="index.html#diagram-p010-d01"', html)

    def test_semantic_reader_keeps_raw_audit_reason_codes_out_of_book_text(self) -> None:
        html = _semantic_source_index_html(
            {
                "title": "Reader without audit noise",
                "summary": {"html_pages": 1, "diagrams_total": 1, "fen_accepted": 0, "accepted_pgn": 0},
                "chapters": [{"title": "Chapter 1", "start_page": 10}],
                "semantic_book": {
                    "schema": SEMANTIC_BOOK_SCHEMA,
                    "book_title": "Reader without audit noise",
                    "summary": {"page_count": 1, "diagram_count": 1},
                    "pages": [
                        {
                            "page_number": 10,
                            "blocks": [
                                {"type": "heading", "level": 2, "text": "Mating motifs"},
                                {"type": "paragraph", "text": "Find the forcing move."},
                                {
                                    "type": "diagram",
                                    "diagram_id": "p010_d01",
                                    "caption": "Diagram 1-1",
                                    "source_page": 10,
                                    "side_to_move": "unknown",
                                    "fen": "",
                                    "fen_status": "unavailable",
                                    "pgn": "",
                                    "book_line": "",
                                    "pgn_status": "unavailable",
                                    "review_status": "needs_review",
                                    "exercise_id": "ex_1_1",
                                    "board_crop_fail_reason": "board_bbox_not_tight_8x8",
                                    "marker_search_zones": {"right": [1, 2, 3, 4]},
                                },
                            ],
                        }
                    ],
                },
            }
        )

        self.assertIn("Find the forcing move.", html)
        self.assertIn("Audit", html)
        self.assertNotIn("board_bbox_not_tight_8x8", html)
        self.assertNotIn("marker_search_zones", html)


if __name__ == "__main__":
    unittest.main()
