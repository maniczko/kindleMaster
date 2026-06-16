from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from chess_position_recognizer import (
    MAX_EMPTY_TEMPLATE_VARIANTS,
    MAX_DARK_QUEEN_AMBIGUITY_CONFIDENCE,
    MIN_SPARSE_POSITION_CONFIDENCE,
    _border_line_square_candidates,
    _border_refinement_is_local,
    _clear_piece_template_cache,
    _dense_board_area_crop,
    _dominant_board_content_square_crop,
    _estimate_board_alternation_signal,
    _has_board_visual_pattern,
    _looks_like_non_piece_cross_marker,
    _looks_like_low_confidence_dark_queen_ambiguity,
    _match_piece_template,
    _normalize_board_square,
    _normalize_piece_cell,
    _recognize_board_with_templates,
    _template_result_from_board,
    ChessFenResult,
    build_fen_from_board,
    detect_board_candidates_in_page_image,
    load_piece_templates,
    recognize_chess_position_from_image,
    recognize_font_board_from_lines,
    validate_fen,
)
from bs4 import BeautifulSoup
from kindle_semantic_cleanup import _normalize_figure_html
from converter import ConversionConfig
from pymupdf_chess_extractor import (
    SCAN_CHESS_PAGE_CANDIDATE_CACHE_VERSION,
    SCAN_CHESS_RECOGNITION_CACHE_VERSION,
    _clamp_bbox,
    _infer_scan_chess_front_matter_metadata,
    _normalize_scan_chess_ocr_text,
    _prefer_scan_chess_recognition_result,
    _rank_scan_chess_page_candidates_by_recognition,
    _recognize_scan_chess_candidate_bbox,
    _scan_chess_apply_verified_crop_label,
    _scan_chess_confirm_final_rendered_crop_recognition,
    _scan_chess_is_partial_separator_crop,
    _recover_expanded_scan_chess_candidate,
    _apply_scan_chess_side_to_move_marker,
    _infer_scan_chess_side_to_move,
    _scan_chess_page_candidates,
    _scan_chess_effective_page_candidate_limit,
    _scan_chess_recognition_pool_size,
    _scan_chess_local_expansion_bboxes,
    _scan_chess_vertical_recovery_bboxes,
    _scan_chess_candidate_review_payload,
    _scan_chess_ocr_html_parts,
    _scan_chess_recalibrate_cached_result,
    _scan_chess_recognition_cache_path,
    _scan_image_for_board_candidates,
)
from scripts.export_chess_fen_review_queue import (
    _case_chess_fen_summary,
    export_chess_fen_review_queue,
)


class ChessFenRecognitionTests(unittest.TestCase):
    def test_review_queue_accepts_premium_corpus_quality_chess_fen(self) -> None:
        case = {
            "quality": {
                "chess_fen": {
                    "diagram_count": 1,
                    "fen_count": 0,
                    "records": [
                        {
                            "page": 12,
                            "filename": "scan_chess_p012_01.png",
                            "placement": "8/8/8/8/8/8/8/8",
                            "confidence": 0.4,
                            "warnings": ["white_king_count_invalid", "black_king_count_invalid"],
                            "requires_review": True,
                            "bbox": [0, 0, 10, 10],
                            "method": "image-template-board",
                        }
                    ],
                }
            }
        }
        self.assertEqual(_case_chess_fen_summary(case)["diagram_count"], 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "premium.json"
            output_dir = Path(temp_dir) / "queue"
            crop_source_dir = Path(temp_dir) / "source_crops"
            crop_source_dir.mkdir()
            (crop_source_dir / "scan_chess_p012_01.png").write_bytes(b"fake-png-for-review-queue")
            report_path.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")

            summary = export_chess_fen_review_queue(
                report_path,
                output_dir=output_dir,
                max_items=10,
                crop_source_dirs=[crop_source_dir],
            )
            copied_crop = output_dir / "crops" / "scan_chess_p012_01.png"
            copied_crop_exists = copied_crop.exists()
            draft_path = Path(summary["manual_verification_draft_path"])
            draft_path_exists = draft_path.exists()
            draft_rows = [json.loads(line) for line in draft_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            review_sheet_path = Path(summary["manual_review_sheet_path"])
            review_sheet_exists = review_sheet_path.exists()
            review_sheet_html = review_sheet_path.read_text(encoding="utf-8") if review_sheet_exists else ""

        self.assertEqual(summary["diagram_count"], 1)
        self.assertEqual(summary["manual_review_count"], 1)
        self.assertEqual(summary["exported_count"], 1)
        self.assertEqual(summary["crop_file_count"], 1)
        self.assertEqual(summary["missing_crop_count"], 0)
        self.assertEqual(summary["manual_verification_draft_count"], 1)
        self.assertEqual(summary["deterministic_suggestion_count"], 0)
        self.assertTrue(copied_crop_exists)
        self.assertIn("crop_source", summary["queue"][0])
        self.assertTrue(draft_path_exists)
        self.assertEqual(draft_rows[0]["label_status"], "needs_manual_fen")
        self.assertFalse(draft_rows[0]["accepted_for_corpus"])
        self.assertEqual(draft_rows[0]["fen"], "")
        self.assertTrue(review_sheet_exists)
        self.assertIn("Chess FEN Manual Review", review_sheet_html)
        self.assertIn("scan_chess_p012_01.png", review_sheet_html)
        self.assertIn("needs manual FEN", review_sheet_html)
        self.assertEqual(summary["reason_counts"], {"invalid_king_count": 1})
        self.assertEqual(
            summary["review_priority_counts"],
            {
                "ready_for_human_acceptance": 0,
                "candidate_matches_review_crop": 0,
                "needs_manual_fen": 1,
            },
        )
        self.assertIn("build_chess_fen_label_aids.py", summary["label_aids_command"])
        self.assertIn("promote_chess_fen_label_draft.py", summary["label_promote_command"])
        self.assertIn("build_chess_piece_templates.py", summary["template_build_command"])
        self.assertIn("evaluate_chess_fen_corpus.py", summary["profile_eval_command"])
        self.assertEqual(summary["next_commands"]["label_promote_command"], summary["label_promote_command"])

    def test_scan_chess_candidate_cache_version_covers_expanded_recovery(self) -> None:
        self.assertGreaterEqual(SCAN_CHESS_PAGE_CANDIDATE_CACHE_VERSION, 17)

    def test_review_queue_manual_draft_prioritizes_safe_deterministic_suggestions(self) -> None:
        from scripts.export_chess_fen_review_queue import _build_manual_verification_draft

        rows = _build_manual_verification_draft(
            [
                {
                    "id": "needs_full_manual_label",
                    "page": 2,
                    "filename": "scan_chess_p002_01.png",
                    "crop_path": "crops/scan_chess_p002_01.png",
                    "candidate_fen": "",
                    "candidate_placement": "8/8/8/8/8/8/8/8",
                    "review_crop_fen": "",
                    "review_crop_requires_review": True,
                    "review_crop_confidence": 0.12,
                    "candidate_matches_review_crop": False,
                },
                {
                    "id": "safe_deterministic_label",
                    "page": 1,
                    "filename": "scan_chess_p001_01.png",
                    "crop_path": "crops/scan_chess_p001_01.png",
                    "candidate_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "candidate_placement": "4k3/8/8/8/8/8/8/4K3",
                    "review_crop_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "review_crop_requires_review": False,
                    "review_crop_confidence": 0.91,
                    "candidate_matches_review_crop": True,
                },
            ]
        )

        self.assertEqual(rows[0]["id"], "safe_deterministic_label")
        self.assertEqual(rows[0]["deterministic_suggested_fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertEqual(rows[1]["id"], "needs_full_manual_label")

    def test_scan_chess_recognition_cache_version_covers_recognizer_acceptance_changes(self) -> None:
        self.assertGreaterEqual(SCAN_CHESS_RECOGNITION_CACHE_VERSION, 6)

    def test_scan_chess_recognition_cache_path_is_threshold_independent(self) -> None:
        image_data = b"same-crop"
        bbox = (1.0, 2.0, 81.0, 82.0)
        templates = {"K": [Image.new("L", (8, 8), 0)]}

        high_threshold = _scan_chess_recognition_cache_path(
            image_data,
            bbox=bbox,
            min_confidence=0.84,
            piece_templates=templates,
        )
        calibrated_threshold = _scan_chess_recognition_cache_path(
            image_data,
            bbox=bbox,
            min_confidence=0.835,
            piece_templates=templates,
        )

        self.assertEqual(high_threshold, calibrated_threshold)

    def test_cached_scan_chess_result_is_recalibrated_to_current_threshold(self) -> None:
        cached = ChessFenResult(
            fen="",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.838,
            side_to_move="w",
            method="image-template-board",
            warnings=["piece_template_confidence_below_threshold", "side_to_move_inferred"],
            requires_review=True,
            board_detected=True,
        )

        accepted = _scan_chess_recalibrate_cached_result(cached, min_confidence=0.835)
        still_review = _scan_chess_recalibrate_cached_result(cached, min_confidence=0.84)

        self.assertFalse(accepted.requires_review)
        self.assertEqual(accepted.fen, "8/8/8/8/8/8/7k/7K w - - 0 1")
        self.assertNotIn("piece_template_confidence_below_threshold", accepted.warnings)
        self.assertTrue(still_review.requires_review)
        self.assertEqual(still_review.fen, "")

    def test_conversion_config_has_verified_crop_label_path(self) -> None:
        config = ConversionConfig()

        self.assertEqual(
            config.chess_fen_verified_crop_labels_path,
            "reference_inputs/chess_fen/labels/fundamenty_verified_crop_labels.jsonl",
        )

    def test_scan_chess_partial_separator_crop_is_rejected(self) -> None:
        image = Image.new("L", (360, 360), 255)
        draw = ImageDraw.Draw(image)
        for x0 in (0, 290):
            draw.rectangle((x0, 0, x0 + 68, 359), outline=0, width=4)
            for row in range(8):
                for col in range(2):
                    if (row + col) % 2 == 0:
                        x = x0 + col * 34
                        y = row * 45
                        draw.rectangle((x, y, x + 33, y + 44), fill=235)
                        for offset in range(-40, 45, 8):
                            draw.line((x + offset, y + 44, x + offset + 44, y), fill=0, width=1)
        for idx, rank in enumerate("87654321"):
            draw.text((250, 16 + idx * 42), rank, fill=0)

        self.assertTrue(_scan_chess_is_partial_separator_crop(image))

    def test_scan_chess_full_sparse_board_is_not_separator_crop(self) -> None:
        image = Image.new("L", (360, 360), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 359, 359), outline=0, width=4)
        cell = 45
        for row in range(8):
            for col in range(8):
                if (row + col) % 2 == 0:
                    x = col * cell
                    y = row * cell
                    draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=235)
                    for offset in range(-cell, cell, 8):
                        draw.line((x + offset, y + cell - 1, x + offset + cell - 1, y), fill=0, width=1)
        draw.ellipse((58, 150, 86, 190), outline=0, width=4)
        draw.rectangle((285, 240, 310, 285), outline=0, width=4)

        self.assertFalse(_scan_chess_is_partial_separator_crop(image))

    def test_empty_template_variant_cap_preserves_sparse_holdout_diversity(self) -> None:
        self.assertGreaterEqual(MAX_EMPTY_TEMPLATE_VARIANTS, 1200)

    def test_sparse_threshold_keeps_only_narrow_review_margin(self) -> None:
        self.assertGreaterEqual(MIN_SPARSE_POSITION_CONFIDENCE, 0.832)
        self.assertLess(MIN_SPARSE_POSITION_CONFIDENCE, 0.835)

    def test_instructional_cross_marker_is_not_a_piece_shape(self) -> None:
        marker = Image.new("L", (32, 32), 255)
        draw = ImageDraw.Draw(marker)
        draw.line((4, 4, 27, 27), fill=0, width=4)
        draw.line((27, 4, 4, 27), fill=0, width=4)
        marker_cell = _normalize_piece_cell(marker)

        piece_like = Image.new("L", (32, 32), 255)
        draw = ImageDraw.Draw(piece_like)
        draw.ellipse((9, 4, 23, 18), fill=0)
        draw.rectangle((12, 16, 20, 27), fill=0)
        piece_cell = _normalize_piece_cell(piece_like)

        self.assertTrue(_looks_like_non_piece_cross_marker(marker_cell))
        self.assertFalse(_looks_like_non_piece_cross_marker(piece_cell))

    def test_cross_marker_filter_does_not_suppress_real_dark_square_rook(self) -> None:
        from chess_position_recognizer import load_piece_templates, recognize_chess_position_from_image

        crop = Path("reference_inputs/chess_fen/crops/fundamenty_1_1_scan_chess_p032_runtime_02.png")
        templates = load_piece_templates("reference_inputs/chess_fen/templates/fundamenty_merida_like")

        result = recognize_chess_position_from_image(crop.read_bytes(), piece_templates=templates, min_confidence=0.0)
        h8 = next(square for square in result.squares if square["square"] == "h8")

        self.assertEqual(h8["piece"], "r")
        self.assertNotIn("annotation_cross_marker_suppressed", h8.get("warnings", []))

    def test_inner_checkerboard_crop_ignores_coordinates_and_caption(self) -> None:
        image = Image.new("L", (320, 380), 255)
        draw = ImageDraw.Draw(image)
        left, top, cell = 36, 12, 30
        for row in range(8):
            for col in range(8):
                fill = 245 if (row + col) % 2 == 0 else 90
                x0 = left + col * cell
                y0 = top + row * cell
                draw.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), fill=fill)
        for col, file_name in enumerate("abcdefgh"):
            draw.text((left + col * cell + 8, top + 8 * cell + 4), file_name, fill=0)
        draw.text((left, top + 8 * cell + 36), "Steinitz - Wilson, London 1862", fill=0)

        normalized = _normalize_board_square(image)
        detected, signal = _has_board_visual_pattern(normalized)

        self.assertLess(normalized.size[0], min(image.size))
        self.assertTrue(detected)
        self.assertGreaterEqual(signal, 0.62)

    def test_dominant_content_crop_removes_caption_without_border_lines(self) -> None:
        image = Image.new("L", (332, 332), 255)
        draw = ImageDraw.Draw(image)
        left, top, cell = 47, 8, 30
        for row in range(8):
            for col in range(8):
                fill = 245 if (row + col) % 2 == 0 else 165
                x0 = left + col * cell
                y0 = top + row * cell
                draw.rectangle((x0, y0, x0 + cell - 1, y0 + cell - 1), fill=fill)
                if (row + col) % 2:
                    draw.line((x0 + 2, y0 + cell - 2, x0 + cell - 2, y0 + 2), fill=110)
        for row, rank in enumerate("87654321"):
            draw.text((18, top + row * cell + 8), rank, fill=0)
        for col, file_name in enumerate("abcdefgh"):
            draw.text((left + col * cell + 10, top + 8 * cell + 8), file_name, fill=0)
        draw.text((12, 286), "25. Nh6+ gxh6 26. Rxd7 Black resigned a", fill=0)
        draw.text((12, 310), "move later.", fill=0)

        cropped = _dominant_board_content_square_crop(image)

        self.assertIsNotNone(cropped)
        assert cropped is not None
        self.assertLess(cropped.size[0], min(image.size))
        self.assertGreaterEqual(cropped.size[0], 225)
        self.assertLessEqual(cropped.size[0], 260)
        detected, _signal = _has_board_visual_pattern(cropped)
        self.assertTrue(detected)

    def test_low_confidence_dark_queen_ambiguity_is_review_not_rewrite(self) -> None:
        dark_queen_like = Image.new("L", (32, 32), 255)
        draw = ImageDraw.Draw(dark_queen_like)
        draw.ellipse((7, 4, 25, 22), fill=0)
        draw.rectangle((9, 15, 23, 29), fill=0)
        cell = _normalize_piece_cell(dark_queen_like)

        self.assertLessEqual(MAX_DARK_QUEEN_AMBIGUITY_CONFIDENCE, 0.70)
        self.assertTrue(
            _looks_like_low_confidence_dark_queen_ambiguity(
                "Q",
                0.60,
                cell,
                {"q": np.zeros((1, 32, 32), dtype=np.float32)},
            )
        )
        self.assertFalse(
            _looks_like_low_confidence_dark_queen_ambiguity(
                "Q",
                0.90,
                cell,
                {"q": np.zeros((1, 32, 32), dtype=np.float32)},
            )
        )

    def test_template_matching_prefers_empty_when_piece_margin_is_too_small(self) -> None:
        cell = np.zeros((32, 32), dtype=np.float32)
        cell[10:18, 10:18] = 0.10
        empty_template = np.zeros((1, 32, 32), dtype=np.float32)
        queen_template = np.zeros((1, 32, 32), dtype=np.float32)
        queen_template[:, 10:18, 10:18] = 0.12

        label, confidence = _match_piece_template(
            cell,
            {
                "": empty_template,
                "Q": queen_template,
            },
        )

        self.assertEqual(label, "")
        self.assertGreater(confidence, 0.89)

    def test_queen_color_ambiguity_square_warning_blocks_fen_publication(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        templates = {piece: np.zeros((1, 1, 1), dtype=np.float32) for piece in ["", *"pnbrqkPNBRQK"]}
        squares = [
            {"square": "d8", "piece": "q", "confidence": 0.9, "warnings": ["queen_color_ambiguous_suppressed"]}
        ]

        result = _template_result_from_board(
            board,
            0.95,
            squares,
            templates,
            grid_confidence=0.95,
            bbox=None,
            min_confidence=0.835,
        )

        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("queen_color_ambiguous_suppressed", result.warnings)

    def test_build_fen_from_board_validates_complete_position(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]

        fen = build_fen_from_board(board)

        self.assertEqual(fen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1")
        self.assertEqual(validate_fen(fen), (True, []))

    def test_validate_fen_rejects_pawn_on_back_rank(self) -> None:
        invalid_fen = "8/8/8/8/8/2k5/8/K2P4 w - - 0 1"

        is_valid, warnings = validate_fen(invalid_fen)

        self.assertFalse(is_valid)
        self.assertIn("pawn_on_back_rank", warnings)

    def test_font_board_rows_decode_to_fen_without_ai(self) -> None:
        result = recognize_font_board_from_lines(
            [
                "rnbqkbnr",
                "pppppppp",
                "8",
                "8",
                "8",
                "8",
                "PPPPPPPP",
                "RNBQKBNR",
            ]
        )

        self.assertEqual(result.fen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1")
        self.assertFalse(result.requires_review)
        self.assertEqual(result.method, "font-board")

    def test_unknown_font_glyphs_require_review_without_fen(self) -> None:
        result = recognize_font_board_from_lines(["\uf031" * 8 for _ in range(8)])

        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("font_board_contains_unknown_glyphs", result.warnings)

    def test_skaknew_font_board_rows_decode_to_fen_without_ai(self) -> None:
        result = recognize_font_board_from_lines(
            [
                "8rmblkans",
                "7opopopop",
                "60Z0Z0Z0Z",
                "5Z0Z0Z0Z0",
                "40Z0Z0Z0Z",
                "3Z0Z0Z0Z0",
                "2POPOPOPO",
                "1SNAQJBMR",
            ],
            min_confidence=0.84,
        )

        self.assertEqual(result.method, "font-board")
        self.assertFalse(result.requires_review)
        self.assertEqual(
            result.fen,
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
        )

    def test_skaknew_tactics_rows_decode_sparse_problem_to_fen(self) -> None:
        result = recognize_font_board_from_lines(
            [
                "80L0Z0Z0Z",
                "7Z0Z0Z0Z0",
                "60Z0Z0Z0Z",
                "5Z0Z0Z0Z0",
                "40Z0Z0Z0Z",
                "3j0ZKZ0Z0",
                "20Z0Z0Z0Z",
                "1Z0Z0Z0Z0",
            ],
            min_confidence=0.84,
        )

        self.assertEqual(result.fen, "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1")
        self.assertFalse(result.requires_review)

    def test_image_board_detection_does_not_invent_piece_fen(self) -> None:
        image = _board_png((320, 320))

        result = recognize_chess_position_from_image(image)

        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertTrue(result.board_detected)
        self.assertIn("piece_templates_unavailable", result.warnings)

    def test_image_template_recognizer_generates_fen_only_with_complete_templates(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, templates = _labeled_board_png_and_templates(board)

        result = recognize_chess_position_from_image(
            image_data,
            piece_templates=templates,
            min_confidence=0.85,
        )

        self.assertEqual(result.fen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1")
        self.assertEqual(result.method, "image-template-board")
        self.assertFalse(result.requires_review)

    def test_image_template_recognizer_blocks_incomplete_template_set(self) -> None:
        board = [
            ["k", "", "", "", "", "", "", ""],
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            ["", "", "", "", "K", "", "", ""],
        ]
        image_data, templates = _labeled_board_png_and_templates(board)
        limited_templates = {"": templates[""], "K": templates["K"], "k": templates["k"]}

        result = recognize_chess_position_from_image(
            image_data,
            piece_templates=limited_templates,
            min_confidence=0.85,
        )

        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("piece_template_set_incomplete", result.warnings)

    def test_sparse_valid_position_requires_stronger_confidence(self) -> None:
        board = [
            [""] * 8,
            [""] * 8,
            [""] * 8,
            ["", "", "", "", "", "", "K", ""],
            ["", "", "", "p", "k", "P", "", ""],
            [""] * 8,
            ["", "", "", "", "", "P", "", ""],
            [""] * 8,
        ]
        complete_templates = {"": [Image.new("L", (10, 10), 255)]}
        complete_templates.update({piece: [Image.new("L", (10, 10), 0)] for piece in "KQRBNPkqrbnp"})

        result = _template_result_from_board(
            board,
            0.83,
            [],
            complete_templates,
            grid_confidence=0.83,
            bbox=None,
            min_confidence=0.70,
        )

        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("sparse_position_confidence_below_threshold", result.warnings)

    def test_sparse_seven_piece_position_requires_stronger_confidence(self) -> None:
        board = [
            ["n", "", "", "", "", "", "", ""],
            [""] * 8,
            ["P", "", "", "", "", "", "", ""],
            [""] * 8,
            ["p", "", "", "", "", "K", "P", ""],
            ["", "", "", "", "", "", "P", "k"],
            [""] * 8,
            [""] * 8,
        ]
        complete_templates = {"": [Image.new("L", (10, 10), 255)]}
        complete_templates.update({piece: [Image.new("L", (10, 10), 0)] for piece in "KQRBNPkqrbnp"})

        result = _template_result_from_board(
            board,
            0.74,
            [],
            complete_templates,
            grid_confidence=0.74,
            bbox=None,
            min_confidence=0.70,
        )

        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("sparse_position_confidence_below_threshold", result.warnings)

    def test_image_template_preparation_is_cached_for_reused_template_set(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, templates = _labeled_board_png_and_templates(board)
        _clear_piece_template_cache()

        with mock.patch(
            "chess_position_recognizer._prepare_piece_templates",
            wraps=__import__("chess_position_recognizer")._prepare_piece_templates,
        ) as prepare_mock:
            first = recognize_chess_position_from_image(
                image_data,
                piece_templates=templates,
                min_confidence=0.70,
            )
            second = recognize_chess_position_from_image(
                image_data,
                piece_templates=templates,
                min_confidence=0.70,
            )

        _clear_piece_template_cache()
        self.assertEqual(first.fen, second.fen)
        self.assertEqual(prepare_mock.call_count, 1)

    def test_page_candidate_detector_finds_board_like_square_for_review(self) -> None:
        page = Image.new("L", (500, 700), 245)
        board = Image.open(io.BytesIO(_board_png((240, 240)))).convert("L")
        page.paste(board, (120, 200))
        output = io.BytesIO()
        page.save(output, format="PNG")

        candidates = detect_board_candidates_in_page_image(output.getvalue(), max_candidates=2)

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0].fen, "")
        self.assertTrue(candidates[0].requires_review)
        self.assertTrue(candidates[0].board_detected)

    def test_page_candidate_detector_zero_candidates_is_fast_disable(self) -> None:
        self.assertEqual(detect_board_candidates_in_page_image(_board_png((240, 240)), max_candidates=0), [])

    def test_scan_image_for_board_candidates_zero_limits_disable_runtime_scan(self) -> None:
        self.assertEqual(
            _scan_image_for_board_candidates(
                _board_png((240, 240)),
                page_num=0,
                filename="board.png",
                config=ConversionConfig(chess_fen_scan_max_pages=0),
            ),
            [],
        )
        self.assertEqual(
            _scan_image_for_board_candidates(
                _board_png((240, 240)),
                page_num=0,
                filename="board.png",
                config=ConversionConfig(chess_fen_scan_candidates_per_page=0),
            ),
            [],
        )

    def test_load_piece_templates_uses_fen_piece_filename_prefixes(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            Image.new("L", (32, 32), 255).save(root / "empty-light.png")
            Image.new("L", (32, 32), 0).save(root / "K-white.png")

            templates = load_piece_templates(root)

        self.assertIn("", templates)
        self.assertIn("K", templates)

    def test_fundamenty_seed_labels_have_required_fen_schema(self) -> None:
        label_path = Path("reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl")
        self.assertTrue(label_path.exists(), "seed label file must exist")

        records = [json.loads(line) for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertGreaterEqual(len(records), 3)
        for record in records:
            self.assertIn("crop_path", record)
            self.assertIn("fen", record)
            self.assertIn("source_pdf", record)
            self.assertIn("page", record)
            self.assertIn("diagram_index", record)
            self.assertTrue(Path(record["crop_path"]).exists(), record["crop_path"])
            is_valid, warnings = validate_fen(record["fen"])
            self.assertTrue(is_valid, warnings)

    def test_template_builder_extracts_64_cells_from_labeled_board(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            output_dir = root / "templates"
            summary = build_templates_from_labels(labels_path, output_dir=output_dir)

            self.assertEqual(summary["boards_processed"], 1)
            self.assertGreaterEqual(summary["template_count"], 64)
            self.assertTrue((output_dir / "K-white-001.png").exists())
            self.assertTrue((output_dir / "empty-light-001.png").exists())

    def test_template_builder_cleans_stale_generated_templates_by_default(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels

        board = [
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            ["k"] + [""] * 7,
            [""] * 8,
            [""] * 7 + ["K"],
            [""] * 8,
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "crop_path": str(crop_path),
                        "fen": "8/8/8/8/k7/8/7K/8 w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_dir = root / "templates"
            output_dir.mkdir()
            Image.new("L", (64, 64), 0).save(output_dir / "P-white-999.png")
            (output_dir / "notes.txt").write_text("keep me", encoding="utf-8")

            summary = build_templates_from_labels(labels_path, output_dir=output_dir)
            templates = load_piece_templates(output_dir)

            self.assertGreaterEqual(summary["removed_stale_files"], 1)
            self.assertFalse((output_dir / "P-white-999.png").exists())
            self.assertTrue((output_dir / "notes.txt").exists())
            self.assertIn("K", templates)
            self.assertIn("k", templates)
            self.assertNotIn("P", templates)

    def test_evaluate_chess_fen_recognizer_reports_exact_fen_accuracy(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template_dir = root / "templates"
            build_templates_from_labels(labels_path, output_dir=template_dir)

            result = evaluate_chess_fen_recognizer(labels_path, template_dir=template_dir, min_confidence=0.80)

            self.assertEqual(result["case_count"], 1)
            self.assertEqual(result["exact_fen_accuracy"], 1.0)
            self.assertEqual(result["fen_count"], 1)
            self.assertEqual(result["false_positive_count"], 0)
            self.assertEqual(result["false_positive_rate"], 0.0)
            self.assertFalse(result["cases"][0]["false_positive"])
            self.assertEqual(result["square_accuracy"], 1.0)
            self.assertEqual(result["per_piece_accuracy"]["K"], 1.0)
            self.assertEqual(result["per_piece_accuracy"]["empty"], 1.0)

    def test_chess_fen_profile_holdout_evaluator_trains_without_holdout_rows(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_fen_profile_holdout import evaluate_chess_fen_profile_holdout

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            rows = [
                {
                    "id": f"synthetic_start_{index:02d}",
                    "crop_path": str(crop_path),
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                    "source_pdf": "synthetic",
                    "page": index,
                    "diagram_index": 1,
                    "verified_by": "unit-test",
                    "verified_at": "2026-05-27",
                    "notes": "verified synthetic holdout fixture",
                }
                for index in range(10)
            ]
            labels_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = evaluate_chess_fen_profile_holdout(
                labels_path,
                min_confidence=0.80,
                min_exact_accuracy=0.90,
                fold_count=5,
                holdout_fold=0,
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["train_label_count"], 8)
        self.assertEqual(result["holdout_label_count"], 2)
        self.assertEqual(result["template_summary"]["boards_processed"], 8)
        self.assertEqual(result["holdout_eval"]["case_count"], 2)
        self.assertEqual(result["holdout_eval"]["exact_fen_accuracy"], 1.0)
        self.assertEqual(result["holdout_eval"]["false_positive_count"], 0)
        self.assertEqual(result["holdout_cases"], [])
        self.assertEqual(result["policy"], "templates_built_from_train_split_only")

    def test_chess_fen_profile_holdout_rejects_review_only_labels(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_fen_profile_holdout import evaluate_chess_fen_profile_holdout

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            labels_path = root / "manual_label_template.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "needs_manual_fen",
                        "crop_path": str(crop_path),
                        "fen": "",
                        "verified_by": "",
                        "verified_at": "",
                        "label_status": "needs_manual_fen",
                        "notes": "fill fen manually",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = evaluate_chess_fen_profile_holdout(labels_path)

        self.assertEqual(result["status"], "failed")
        self.assertIn("label_validation_failed", result["reasons"])
        self.assertEqual(result["label_validation"]["valid_label_count"], 0)

    def test_fundamenty_seed_eval_passes_90_percent_gate_without_false_positives(self) -> None:
        from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer

        result = evaluate_chess_fen_recognizer(
            "reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl",
            template_dir="reference_inputs/chess_fen/templates/fundamenty_merida_like",
            min_confidence=0.84,
            min_exact_accuracy=0.90,
        )

        self.assertEqual(result["status"], "passed")
        self.assertGreaterEqual(result["case_count"], 40)
        self.assertGreaterEqual(result["exact_fen_accuracy"], 0.90)
        self.assertEqual(result["false_positive_count"], 0)
        self.assertGreaterEqual(result["square_accuracy"], 0.995)

    def test_fundamenty_seed_eval_default_confidence_matches_90_percent_gate(self) -> None:
        from scripts.evaluate_chess_fen_recognizer import (
            DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
            evaluate_chess_fen_recognizer,
        )

        result = evaluate_chess_fen_recognizer(
            "reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl",
            template_dir="reference_inputs/chess_fen/templates/fundamenty_merida_like",
            min_exact_accuracy=0.90,
        )

        self.assertEqual(DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE, 0.835)
        self.assertEqual(result["min_confidence"], 0.835)
        self.assertEqual(result["status"], "passed")
        self.assertGreaterEqual(result["exact_fen_accuracy"], 0.90)
        self.assertEqual(result["false_positive_count"], 0)

    def test_chess_fen_corpus_evaluator_reads_manifest_profiles(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "synthetic_start",
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template_root = root / "templates"
            template_dir = template_root / "synthetic_profile"
            build_templates_from_labels(labels_path, output_dir=template_dir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root_dir": ".",
                        "cases": [
                            {
                                "id": "synthetic_chess_pdf",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "language": "pl",
                                "chess_fen_seed_labels": str(labels_path),
                                "chess_fen_template_profile": "synthetic_profile",
                                "chess_fen_seed_exact_accuracy_min": 0.90,
                                "chess_fen_seed_min_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_chess_fen_corpus(
                manifest_path,
                template_root=template_root,
                min_confidence=0.80,
                default_min_exact_accuracy=0.90,
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["evaluated_case_count"], 1)
        self.assertEqual(result["failed_case_count"], 0)
        self.assertEqual(result["total_false_positive_count"], 0)
        self.assertEqual(result["overall_exact_fen_accuracy"], 1.0)
        self.assertEqual(result["cases"][0]["template_profile"], "synthetic_profile")
        self.assertEqual(result["cases"][0]["label_validation"]["status"], "passed")

    def test_chess_fen_corpus_evaluator_can_require_multiple_profiles(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "synthetic_start",
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template_root = root / "templates"
            build_templates_from_labels(labels_path, output_dir=template_root / "synthetic_profile")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "cases": [
                            {
                                "id": "synthetic_chess_pdf",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "chess_fen_seed_labels": str(labels_path),
                                "chess_fen_template_profile": "synthetic_profile",
                                "chess_fen_seed_min_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_chess_fen_corpus(
                manifest_path,
                template_root=template_root,
                min_confidence=0.80,
                min_profile_count=2,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["evaluated_case_count"], 1)
        self.assertEqual(result["missing_profile_count"], 1)
        self.assertIn("below required minimum 2", result["reasons"][0])
        self.assertEqual(len(result["next_required_actions"]), 1)
        self.assertIn("add 1 real scanned chess FEN profile", result["next_required_actions"][0])
        self.assertIn("at least 20 manually verified labels", result["next_required_actions"][0])

    def test_chess_fen_corpus_evaluator_reports_font_board_candidates_separately(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            verified_labels = root / "verified_labels.jsonl"
            verified_labels.write_text(
                json.dumps(
                    {
                        "id": "verified_start",
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic_scan.pdf",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            candidate_labels = root / "font_board_candidates.jsonl"
            rows = [
                {
                    "id": f"font_candidate_{index}",
                    "source_pdf": "local/tactits.pdf",
                    "page": 69,
                    "diagram_index": index,
                    "input_type": "font_board_text",
                    "candidate_fen": "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1",
                    "candidate_confidence": 0.96,
                    "candidate_requires_review": False,
                    "fen": "",
                    "label_status": "needs_manual_fen",
                }
                for index in range(1, 4)
            ]
            candidate_labels.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            template_root = root / "templates"
            build_templates_from_labels(verified_labels, output_dir=template_root / "verified_profile")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "cases": [
                            {
                                "id": "verified_scan_profile",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "language": "pl",
                                "chess_fen_seed_labels": str(verified_labels),
                                "chess_fen_template_profile": "verified_profile",
                                "chess_fen_seed_min_count": 1,
                            },
                            {
                                "id": "font_board_review_profile",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "language": "en",
                                "chess_fen_font_board_candidate_labels": str(candidate_labels),
                                "chess_fen_candidate_fen_coverage_min": 0.90,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_chess_fen_corpus(
                manifest_path,
                template_root=template_root,
                min_confidence=0.80,
                min_profile_count=2,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["evaluated_case_count"], 1)
        self.assertEqual(result["missing_profile_count"], 1)
        self.assertEqual(result["font_board_candidate_profile_count"], 1)
        self.assertEqual(result["font_board_candidate_failed_count"], 0)
        self.assertEqual(result["font_board_candidate_status"], "review_ready")
        self.assertFalse(result["font_board_candidate_profiles"][0]["accepted_for_corpus"])
        self.assertEqual(result["font_board_candidate_profiles"][0]["candidate_fen_coverage"], 1.0)
        self.assertIn("add 1 real scanned chess FEN profile", result["next_required_actions"][0])

    def test_chess_fen_corpus_evaluator_fails_bad_font_board_candidate_gate(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            verified_labels = root / "verified_labels.jsonl"
            verified_labels.write_text(
                json.dumps(
                    {
                        "id": "verified_start",
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic_scan.pdf",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            candidate_labels = root / "font_board_candidates_bad.jsonl"
            candidate_labels.write_text(
                json.dumps(
                    {
                        "id": "font_candidate_accepted_too_early",
                        "source_pdf": "local/tactits.pdf",
                        "page": 69,
                        "diagram_index": 1,
                        "input_type": "font_board_text",
                        "candidate_fen": "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1",
                        "fen": "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1",
                        "label_status": "accepted",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template_root = root / "templates"
            build_templates_from_labels(verified_labels, output_dir=template_root / "verified_profile")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "cases": [
                            {
                                "id": "verified_scan_profile",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "chess_fen_seed_labels": str(verified_labels),
                                "chess_fen_template_profile": "verified_profile",
                                "chess_fen_seed_min_count": 1,
                            },
                            {
                                "id": "font_board_bad_review_profile",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "chess_fen_font_board_candidate_labels": str(candidate_labels),
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_chess_fen_corpus(
                manifest_path,
                template_root=template_root,
                min_confidence=0.80,
                min_profile_count=1,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["evaluated_case_count"], 1)
        self.assertEqual(result["missing_profile_count"], 0)
        self.assertEqual(result["failed_case_count"], 0)
        self.assertEqual(result["font_board_candidate_failed_count"], 1)
        self.assertEqual(result["font_board_candidate_status"], "failed")
        self.assertIn("font-board candidate profile", result["reasons"][0])
        self.assertTrue(
            any(
                "review-only candidate file contains accepted fen labels" in reason
                for reason in result["font_board_candidate_profiles"][0]["reasons"]
            )
        )

    def test_chess_fen_corpus_evaluator_rejects_tiny_seed_profile_by_default(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "tiny_profile_start",
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template_root = root / "templates"
            build_templates_from_labels(labels_path, output_dir=template_root / "tiny_profile")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "cases": [
                            {
                                "id": "tiny_chess_pdf",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "chess_fen_seed_labels": str(labels_path),
                                "chess_fen_template_profile": "tiny_profile",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_chess_fen_corpus(
                manifest_path,
                template_root=template_root,
                min_confidence=0.80,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_case_count"], 1)
        self.assertEqual(result["default_min_seed_label_count"], 20)
        self.assertEqual(result["cases"][0]["label_validation"]["status"], "passed")
        self.assertEqual(result["cases"][0]["label_validation"]["valid_label_count"], 1)
        self.assertEqual(result["cases"][0]["min_seed_label_count"], 20)
        self.assertEqual(result["cases"][0]["failure_reason"], "seed_label_count_below_minimum")

    def test_chess_fen_corpus_evaluator_rejects_review_only_labels_before_accuracy_gate(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_fen_corpus import evaluate_chess_fen_corpus

        board = [[""] * 8 for _ in range(8)]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "review_only_labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "draft_review_row",
                        "crop_path": str(crop_path),
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                        "label_status": "needs_manual_fen",
                        "verified_by": "",
                        "verified_at": "",
                        "notes": "Fill FEN manually. This review row is not accepted for corpus proof.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "cases": [
                            {
                                "id": "draft_chess_pdf",
                                "document_class": "diagram_training_book",
                                "input_type": "pdf",
                                "chess_fen_seed_labels": str(labels_path),
                                "chess_fen_template_profile": "draft_profile",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_chess_fen_corpus(
                manifest_path,
                template_root=root / "templates",
                min_confidence=0.84,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_case_count"], 1)
        self.assertEqual(result["cases"][0]["label_validation"]["status"], "failed")
        issue_codes = {issue["code"] for issue in result["cases"][0]["label_validation"]["issues"]}
        self.assertIn("review_only_label_status", issue_codes)
        self.assertIn("verified_by_missing", issue_codes)
        self.assertIn("verified_at_missing", issue_codes)
        self.assertIn("placeholder_notes", issue_codes)

    def test_chess_pdf_discovery_helpers_keep_sampling_bounded(self) -> None:
        from scripts.discover_chess_pdf_candidates import _candidate_status, _iter_pdf_paths, _sample_page_indexes

        self.assertEqual(_sample_page_indexes(10, 5), [0, 2, 4, 7, 9])
        self.assertEqual(_sample_page_indexes(3, 10), [0, 1, 2])
        self.assertEqual(_sample_page_indexes(0, 5), [])
        self.assertEqual(_candidate_status(2, 3, min_candidate_pages=2), "candidate")
        self.assertEqual(_candidate_status(1, 1, min_candidate_pages=2), "weak_candidate")
        self.assertEqual(_candidate_status(0, 0, min_candidate_pages=2), "no_board_candidates")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.pdf"
            second = root / "nested" / "second.PDF"
            second.parent.mkdir()
            first.write_bytes(b"%PDF-1.4\n% first\n")
            second.write_bytes(b"%PDF-1.4\n% second\n")

            paths = _iter_pdf_paths([root], max_files=1)

        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].name, "first.pdf")

    def test_chess_fen_profile_intake_creates_review_only_seed_package(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer
        from scripts.prepare_chess_fen_profile_intake import (
            normalize_chess_fen_profile_id,
            prepare_chess_fen_profile_intake_from_crop_manifest,
        )

        self.assertEqual(normalize_chess_fen_profile_id("Second Chess Book.pdf"), "second_chess_book")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "crop.png"
            crop_path.write_bytes(_labeled_board_png_and_templates([[""] * 8 for _ in range(8)])[0])
            crop_manifest = {
                "source_pdf": "reference_inputs/pdf/second_chess_book.pdf",
                "manifest_path": str(root / "crop_manifest.json"),
                "crops": [
                    {
                        "source_pdf": "reference_inputs/pdf/second_chess_book.pdf",
                        "page": 3,
                        "candidate": 1,
                        "crop_path": str(crop_path),
                    }
                ],
            }

            result = prepare_chess_fen_profile_intake_from_crop_manifest(
                crop_manifest,
                profile_id="Second Chess Book.pdf",
                output_dir=root / "intake",
                min_seed_labels=1,
            )

            candidate_rows = [
                json.loads(line)
                for line in Path(result["candidate_labels_review"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            manifest_draft = json.loads(Path(result["manifest_case_draft"]).read_text(encoding="utf-8"))
            eval_result = evaluate_chess_fen_recognizer(
                result["candidate_labels_review"],
                template_dir=root / "empty_templates",
                min_confidence=0.84,
                min_exact_accuracy=0.90,
            )

        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertEqual(result["candidate_label_count"], 1)
        self.assertEqual(candidate_rows[0]["fen"], "")
        self.assertEqual(candidate_rows[0]["label_status"], "needs_manual_fen")
        self.assertIn(
            "reference_inputs/chess_fen/labels/second_chess_book_seed_positions.jsonl",
            manifest_draft["chess_fen_seed_labels"].replace("\\", "/"),
        )
        self.assertEqual(eval_result["status"], "failed")
        self.assertEqual(eval_result["exact_fen_accuracy"], 0.0)

    def test_chess_fen_label_aids_do_not_generate_accepted_fen(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_fen_label_aids import build_chess_fen_label_aids
        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "candidate_labels_review.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "candidate_start",
                        "crop_path": str(crop_path),
                        "page": 1,
                        "diagram_index": 1,
                        "candidate_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "candidate_confidence": 0.91,
                        "fen": "",
                        "label_status": "needs_manual_fen",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = build_chess_fen_label_aids(labels_path, output_dir=root / "aids")
            template_rows = [
                json.loads(line)
                for line in Path(result["manual_label_template"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            validation = validate_chess_fen_labels(result["manual_label_template"])
            contact_sheet_exists = Path(result["contact_sheet"]).exists()
            aid_exists = Path(result["aids"][0]["aid_path"]).exists()
            openai_request_path = Path(result["openai_label_assist_requests"])
            openai_request = json.loads(openai_request_path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertEqual(result["policy"], "review_only_no_fen_generation")
        self.assertEqual(result["openai_policy"], "label_assist_review_only_no_corpus_promotion")
        self.assertEqual(result["aid_count"], 1)
        self.assertEqual(result["openai_request_count"], 1)
        self.assertTrue(contact_sheet_exists)
        self.assertTrue(aid_exists)
        self.assertEqual(template_rows[0]["fen"], "")
        self.assertEqual(template_rows[0]["verified_by"], "")
        self.assertEqual(validation["status"], "failed")
        self.assertEqual(validation["valid_label_count"], 0)
        self.assertFalse(openai_request["accepted_for_corpus"])
        self.assertEqual(openai_request["review_policy"], "label_assist_review_only_no_corpus_promotion")
        self.assertEqual(openai_request["body"]["input"][0]["content"][1]["type"], "input_image")
        self.assertTrue(openai_request["body"]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,"))
        self.assertIn("candidate_fen", openai_request["body"]["input"][0]["content"][0]["text"])

    def test_chess_fen_label_validator_accepts_source_crop_path_and_rejects_missing_paths(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            labels_path = root / "labels.jsonl"
            valid_record = {
                "id": "source_path_record",
                "source_crop_path": str(crop_path),
                "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                "verified_by": "unit-test",
                "verified_at": "2026-05-28",
                "label_status": "verified",
            }
            invalid_record = {
                "id": "missing_path_record",
                "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                "verified_by": "unit-test",
                "verified_at": "2026-05-28",
                "label_status": "verified",
            }
            labels_path.write_text(
                "\n".join(json.dumps(record) for record in [valid_record, invalid_record]) + "\n",
                encoding="utf-8",
            )

            result = validate_chess_fen_labels(labels_path)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["valid_label_count"], 1)
        self.assertEqual(result["issues"], [{"line": 2, "id": "missing_path_record", "code": "crop_path_missing"}])

    def test_label_assist_import_creates_manual_draft_not_verified_labels(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.import_chess_fen_label_assist import import_chess_fen_label_assist
        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            candidate_labels = root / "candidate_labels_review.jsonl"
            candidate_labels.write_text(
                json.dumps(
                    {
                        "id": "candidate_start",
                        "crop_path": str(crop_path),
                        "page": 1,
                        "diagram_index": 1,
                        "fen": "",
                        "label_status": "needs_manual_fen",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            responses = root / "responses.jsonl"
            suggested_fen = "8/8/8/3k4/8/8/4K3/8 w - - 0 1"
            responses.write_text(
                json.dumps(
                    {
                        "custom_id": "kindlemaster_chess_fen_label_aid:candidate_start",
                        "response": {
                            "status_code": 200,
                            "body": {
                                "output_text": json.dumps(
                                    {
                                        "approved": True,
                                        "corrected_fen": suggested_fen,
                                        "requires_review": False,
                                        "ambiguous_squares": [],
                                        "issues": [],
                                        "confidence": 0.92,
                                        "notes": "clear board in unit test",
                                    }
                                )
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = import_chess_fen_label_assist(candidate_labels, responses, output_dir=root / "imported")
            draft_rows = [
                json.loads(line)
                for line in Path(result["manual_verification_draft"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            validation = validate_chess_fen_labels(result["manual_verification_draft"])

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertEqual(result["matched_response_count"], 1)
        self.assertEqual(result["approved_suggestion_count"], 1)
        self.assertEqual(result["ready_for_manual_verification_count"], 1)
        self.assertEqual(draft_rows[0]["fen"], "")
        self.assertEqual(draft_rows[0]["ai_suggested_fen"], suggested_fen)
        self.assertEqual(draft_rows[0]["label_status"], "needs_manual_fen")
        self.assertEqual(draft_rows[0]["verified_by"], "")
        self.assertEqual(validation["status"], "failed")
        issue_codes = {issue["code"] for issue in validation["issues"]}
        self.assertIn("fen_missing", issue_codes)
        self.assertIn("verified_by_missing", issue_codes)
        self.assertIn("verified_at_missing", issue_codes)
        self.assertIn("review_only_label_status", issue_codes)

    def test_label_assist_import_rejects_invalid_ai_suggested_fen(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.import_chess_fen_label_assist import import_chess_fen_label_assist

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            candidate_labels = root / "candidate_labels_review.jsonl"
            candidate_labels.write_text(
                json.dumps({"id": "candidate_bad", "crop_path": str(crop_path), "fen": ""}) + "\n",
                encoding="utf-8",
            )
            responses = root / "responses.jsonl"
            responses.write_text(
                json.dumps(
                    {
                        "id": "candidate_bad",
                        "approved": True,
                        "corrected_fen": "not-a-fen",
                        "requires_review": False,
                        "ambiguous_squares": [],
                        "issues": [],
                        "confidence": 0.99,
                        "notes": "bad unit suggestion",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = import_chess_fen_label_assist(candidate_labels, responses, output_dir=root / "imported")
            draft_rows = [
                json.loads(line)
                for line in Path(result["manual_verification_draft"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(result["invalid_suggestion_count"], 1)
        self.assertEqual(result["ready_for_manual_verification_count"], 0)
        self.assertEqual(draft_rows[0]["ai_suggested_fen"], "")
        self.assertIn("ai_suggested_fen_invalid", draft_rows[0]["ai_issues"])

    def test_promote_label_draft_requires_manual_approval(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.promote_chess_fen_label_draft import promote_chess_fen_label_draft

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            draft = root / "draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "draft_start",
                        "crop_path": str(crop_path),
                        "ai_suggested_fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                        "ai_approved": True,
                        "ai_requires_review": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = promote_chess_fen_label_draft(
                draft,
                output_path=root / "verified.jsonl",
                verified_by="unit-test",
                verified_at="2026-05-27",
            )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["ready_for_profile_gate"])
        self.assertEqual(result["promoted_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "manual_approval_missing")

    def test_promote_label_draft_validates_human_accepted_ai_suggestion(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.promote_chess_fen_label_draft import promote_chess_fen_label_draft
        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            draft = root / "draft.jsonl"
            suggested_fen = "8/8/8/3k4/8/8/4K3/8 w - - 0 1"
            draft.write_text(
                json.dumps(
                    {
                        "id": "draft_start",
                        "crop_path": str(crop_path),
                        "page": 1,
                        "diagram_index": 1,
                        "ai_suggested_fen": suggested_fen,
                        "ai_approved": True,
                        "ai_requires_review": False,
                        "human_verified": True,
                        "ai_confidence": 0.93,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            verified_labels = root / "verified.jsonl"

            result = promote_chess_fen_label_draft(
                draft,
                output_path=verified_labels,
                verified_by="unit-test",
                verified_at="2026-05-27",
            )
            rows = [json.loads(line) for line in verified_labels.read_text(encoding="utf-8").splitlines() if line.strip()]
            validation = validate_chess_fen_labels(verified_labels)

        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertTrue(result["ready_for_profile_gate"])
        self.assertEqual(result["promoted_count"], 1)
        self.assertEqual(rows[0]["fen"], suggested_fen)
        self.assertEqual(rows[0]["verified_by"], "unit-test")
        self.assertEqual(rows[0]["verified_at"], "2026-05-27")
        self.assertEqual(rows[0]["label_status"], "verified")
        self.assertEqual(rows[0]["label_source"], "ai_suggested_fen_after_human_acceptance")
        self.assertEqual(validation["status"], "passed")

    def test_promote_label_draft_validates_human_accepted_deterministic_suggestion(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.promote_chess_fen_label_draft import promote_chess_fen_label_draft
        from scripts.validate_chess_fen_labels import validate_chess_fen_labels

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(_board_png((240, 240)))
            draft = root / "draft.jsonl"
            suggested_fen = "8/8/8/3k4/8/8/4K3/8 w - - 0 1"
            draft.write_text(
                json.dumps(
                    {
                        "id": "draft_deterministic",
                        "crop_path": str(crop_path),
                        "page": 1,
                        "diagram_index": 1,
                        "deterministic_suggested_fen": suggested_fen,
                        "deterministic_confidence": 0.91,
                        "human_verified": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            verified_labels = root / "verified.jsonl"

            result = promote_chess_fen_label_draft(
                draft,
                output_path=verified_labels,
                verified_by="unit-test",
                verified_at="2026-05-31",
            )
            rows = [json.loads(line) for line in verified_labels.read_text(encoding="utf-8").splitlines() if line.strip()]
            validation = validate_chess_fen_labels(verified_labels)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["promoted_count"], 1)
        self.assertEqual(rows[0]["fen"], suggested_fen)
        self.assertEqual(rows[0]["label_source"], "deterministic_suggested_fen_after_human_acceptance")
        self.assertFalse(rows[0]["ai_assisted"])
        self.assertEqual(rows[0]["deterministic_confidence"], 0.91)
        self.assertEqual(validation["status"], "passed")

    def test_chess_fen_profile_ready_rejects_review_only_aid_template(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.build_chess_fen_label_aids import build_chess_fen_label_aids
        from scripts.check_chess_fen_profile_ready import check_chess_fen_profile_ready

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_pdf = root / "second_chess.pdf"
            source_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            candidate_labels = root / "candidate_labels_review.jsonl"
            candidate_labels.write_text(
                json.dumps({"id": "candidate_start", "crop_path": str(crop_path), "fen": ""}) + "\n",
                encoding="utf-8",
            )
            aids = build_chess_fen_label_aids(candidate_labels, output_dir=root / "aids")
            manifest_case = root / "manifest_case_draft.json"
            manifest_case.write_text(
                json.dumps(
                    {
                        "id": "second_chess",
                        "document_class": "diagram_training_book",
                        "input_type": "pdf",
                        "source": str(source_pdf),
                        "target": str(source_pdf),
                        "chess_fen_seed_labels": str(aids["manual_label_template"]),
                        "chess_fen_template_profile": "second_chess",
                    }
                ),
                encoding="utf-8",
            )

            result = check_chess_fen_profile_ready(
                manifest_case,
                template_dir=root / "templates" / "second_chess",
            )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertIn("review_label_artifact_path", {issue["code"] for issue in result["issues"]})
        self.assertIsNone(result["manifest_case_ready"])

    def test_chess_fen_profile_ready_rejects_below_20_verified_labels(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.check_chess_fen_profile_ready import check_chess_fen_profile_ready

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_pdf = root / "second_chess.pdf"
            source_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "second_chess_seed_positions.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "second_chess_001",
                        "source_pdf": str(source_pdf),
                        "page": 1,
                        "diagram_index": 1,
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest_case = root / "manifest_case_draft.json"
            manifest_case.write_text(
                json.dumps(
                    {
                        "id": "second_chess",
                        "document_class": "diagram_training_book",
                        "input_type": "pdf",
                        "source": str(source_pdf),
                        "target": str(source_pdf),
                        "chess_fen_seed_labels": str(labels_path),
                        "chess_fen_template_profile": "second_chess",
                    }
                ),
                encoding="utf-8",
            )

            result = check_chess_fen_profile_ready(
                manifest_case,
                template_dir=root / "templates" / "second_chess",
                min_seed_labels=20,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["label_validation"]["valid_label_count"], 1)
        self.assertIn("seed_label_count_below_minimum", {issue["code"] for issue in result["issues"]})
        self.assertIn("valid_label_count >= 20", " ".join(result["next_required_actions"]))

    def test_chess_fen_profile_ready_accepts_verified_synthetic_20_label_profile(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.check_chess_fen_profile_ready import check_chess_fen_profile_ready

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_pdf = root / "second_chess.pdf"
            source_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            rows = []
            for index in range(20):
                crop_path = root / f"board_{index:02d}.png"
                crop_path.write_bytes(image_data)
                rows.append(
                    {
                        "id": f"second_chess_{index:03d}",
                        "source_pdf": str(source_pdf),
                        "page": index + 1,
                        "diagram_index": 1,
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "verified_by": "unit-test",
                        "verified_at": "2026-05-27",
                    }
                )
            labels_path = root / "second_chess_seed_positions.jsonl"
            labels_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            manifest_case = root / "manifest_case_draft.json"
            manifest_case.write_text(
                json.dumps(
                    {
                        "id": "second_chess",
                        "document_class": "diagram_training_book",
                        "input_type": "pdf",
                        "source": str(source_pdf),
                        "target": str(source_pdf),
                        "chess_fen_seed_labels": str(labels_path),
                        "chess_fen_template_profile": "second_chess",
                        "chess_fen_seed_exact_accuracy_min": 0.90,
                    }
                ),
                encoding="utf-8",
            )

            result = check_chess_fen_profile_ready(
                manifest_case,
                template_dir=root / "templates" / "second_chess",
                min_seed_labels=20,
            )

        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["accepted_for_corpus"])
        self.assertEqual(result["openai_policy"], "review_only_not_used")
        self.assertEqual(result["label_validation"]["valid_label_count"], 20)
        self.assertGreaterEqual(result["evaluation"]["exact_fen_accuracy"], 0.90)
        self.assertEqual(result["evaluation"]["false_positive_count"], 0)
        self.assertIsNotNone(result["manifest_case_ready"])
        self.assertEqual(result["manifest_case_ready"]["chess_fen_seed_min_count"], 20)
        self.assertIn("chess_fen_template_dir", result["manifest_case_ready"])

    def test_font_board_candidate_extractor_creates_review_only_rows(self) -> None:
        from scripts.extract_chess_font_board_candidates import (
            extract_font_board_candidates_from_lines,
            normalize_font_board_row,
        )

        lines = [
            {"text": "80L0Z0Z0Z", "font_names": ["SkakNew-Diagram"], "bbox": [0, 0, 80, 10]},
            {"text": "7Z0Z0Z0Z0", "font_names": ["SkakNew-Diagram"], "bbox": [0, 10, 80, 20]},
            {"text": "60Z0Z0Z0Z", "font_names": ["SkakNew-Diagram"], "bbox": [0, 20, 80, 30]},
            {"text": "5Z0Z0Z0Z0", "font_names": ["SkakNew-Diagram"], "bbox": [0, 30, 80, 40]},
            {"text": "40Z0Z0Z0Z", "font_names": ["SkakNew-Diagram"], "bbox": [0, 40, 80, 50]},
            {"text": "3j0ZKZ0Z0", "font_names": ["SkakNew-Diagram"], "bbox": [0, 50, 80, 60]},
            {"text": "20Z0Z0Z0Z", "font_names": ["SkakNew-Diagram"], "bbox": [0, 60, 80, 70]},
            {"text": "1Z0Z0Z0Z0", "font_names": ["SkakNew-Diagram"], "bbox": [0, 70, 80, 80]},
        ]

        candidates = extract_font_board_candidates_from_lines(
            lines,
            source_pdf="local/tactits.pdf",
            page_number=69,
            id_prefix="tactits",
        )

        self.assertEqual(normalize_font_board_row("80L0Z0Z0Z"), "0L0Z0Z0Z")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "tactits_p069_d01")
        self.assertEqual(candidates[0]["input_type"], "font_board_text")
        self.assertEqual(candidates[0]["font_names"], ["SkakNew-Diagram"])
        self.assertEqual(candidates[0]["candidate_fen"], "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1")
        self.assertGreaterEqual(candidates[0]["candidate_confidence"], 0.84)
        self.assertEqual(candidates[0]["fen"], "")
        self.assertEqual(candidates[0]["label_status"], "needs_manual_fen")
        self.assertEqual(candidates[0]["raw_rows"][0], "0L0Z0Z0Z")
        self.assertIn("not accepted for corpus proof", candidates[0]["notes"])

    def test_font_board_candidate_evaluator_keeps_candidate_fen_out_of_corpus_labels(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_font_board_candidates import evaluate_chess_font_board_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "candidate_font_board_labels_review.jsonl"
            rows = [
                {
                    "id": f"font_candidate_{index}",
                    "source_pdf": "local/tactits.pdf",
                    "page": 69,
                    "diagram_index": index,
                    "input_type": "font_board_text",
                    "candidate_fen": "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1",
                    "candidate_confidence": 0.96,
                    "candidate_requires_review": False,
                    "fen": "",
                    "label_status": "needs_manual_fen",
                }
                for index in range(1, 4)
            ]
            labels_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = evaluate_chess_font_board_candidates(labels_path, min_candidate_fen_coverage=0.90)

        self.assertEqual(result["status"], "review_ready")
        self.assertFalse(result["accepted_for_corpus"])
        self.assertEqual(result["candidate_fen_coverage"], 1.0)
        self.assertEqual(result["valid_candidate_fen_coverage"], 1.0)
        self.assertEqual(result["accepted_label_count"], 0)
        self.assertEqual(result["policy"], "candidate_fen_is_review_aid_not_corpus_label")

    def test_font_board_candidate_evaluator_rejects_mixed_accepted_labels(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_font_board_candidates import evaluate_chess_font_board_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "candidate_font_board_labels_review.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "font_candidate_accepted_too_early",
                        "candidate_fen": "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1",
                        "candidate_requires_review": False,
                        "fen": "1Q6/8/8/8/8/k2K4/8/8 w - - 0 1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = evaluate_chess_font_board_candidates(labels_path)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["accepted_label_count"], 1)
        self.assertIn("review-only candidate file contains accepted fen labels", result["reasons"][0])

    def test_candidate_label_gate_rejects_candidate_that_fails_eval(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from scripts.evaluate_chess_fen_candidate_labels import evaluate_candidate_labels

        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(board)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            base_labels = root / "base.jsonl"
            candidate_labels = root / "candidate.jsonl"
            base_labels.write_text(
                json.dumps(
                    {
                        "id": "base_start",
                        "crop_path": str(crop_path),
                        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                    }
                )
                + "\n",
                encoding="utf-8-sig",
            )
            candidate_labels.write_text(
                json.dumps(
                    {
                        "id": "candidate_bad_same_crop",
                        "crop_path": str(crop_path),
                        "fen": "8/8/8/8/8/8/8/4K3 w - - 0 1",
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 2,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = evaluate_candidate_labels(
                base_labels,
                candidate_labels,
                min_confidence=0.70,
                min_base_exact_accuracy=0.99,
                min_candidate_exact_accuracy=1.0,
            )

        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("candidate_below_threshold", result["reasons"])
        self.assertEqual(result["base_before"]["exact_fen_accuracy"], 1.0)

    def test_candidate_label_gate_reports_base_regression(self) -> None:
        from scripts.evaluate_chess_fen_candidate_labels import _base_case_regressions

        regressions = _base_case_regressions(
            [
                {
                    "id": "verified_1",
                    "matched": True,
                    "expected_fen": "8/8/8/8/8/8/4K3/3k4 w - - 0 1",
                    "actual_fen": "8/8/8/8/8/8/4K3/3k4 w - - 0 1",
                }
            ],
            [
                {
                    "id": "verified_1",
                    "matched": False,
                    "expected_fen": "8/8/8/8/8/8/4K3/3k4 w - - 0 1",
                    "actual_fen": "",
                    "warnings": ["piece_template_confidence_below_threshold"],
                    "confidence": 0.42,
                }
            ],
        )

        self.assertEqual(len(regressions), 1)
        self.assertEqual(regressions[0]["reason"], "matched_base_case_became_unmatched")

    def test_fundamenty_seed_templates_round_trip_to_exact_fen(self) -> None:
        import tempfile

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer

        label_path = Path("reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl")

        with tempfile.TemporaryDirectory() as temp_dir:
            template_dir = Path(temp_dir) / "templates"
            build_templates_from_labels(label_path, output_dir=template_dir)

            result = evaluate_chess_fen_recognizer(
                label_path,
                template_dir=template_dir,
                min_confidence=0.70,
                min_exact_accuracy=0.90,
            )

        self.assertGreaterEqual(result["case_count"], 20)
        self.assertEqual(result["fen_count"], result["case_count"])
        self.assertGreaterEqual(result["exact_fen_accuracy"], 0.99)
        self.assertGreaterEqual(result["square_accuracy"], 0.99)
        self.assertEqual(result["per_piece_accuracy"]["n"], 1.0)

    def test_review_queue_exports_unresolved_fen_crops_without_mutating_epub(self) -> None:
        import tempfile

        from scripts.export_chess_fen_review_queue import export_chess_fen_review_queue

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = Image.new("L", (16, 16), 255)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            epub_path = root / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("EPUB/images/scan_chess_p001_01.png", buffer.getvalue())
            smoke_report = root / "smoke.json"
            smoke_report.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "output_epub": str(epub_path),
                                "quality_report": {
                                    "chess_fen": {
                                        "diagram_count": 1,
                                        "fen_count": 0,
                                        "records": [
                                            {
                                                "page": 1,
                                                "filename": "scan_chess_p001_01.png",
                                                "requires_review": True,
                                                "confidence": 0.69,
                                                "placement": "4k3/8/8/8/8/8/8/4K3",
                                                "warnings": ["piece_template_confidence_below_threshold"],
                                                "method": "image-template-board",
                                            }
                                        ],
                                    }
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = export_chess_fen_review_queue(smoke_report, output_dir=root / "queue", max_items=1)

            self.assertEqual(result["exported_count"], 1)
            self.assertEqual(result["queue"][0]["reason"], "valid_below_threshold")
            self.assertEqual(result["queue"][0]["review_policy"], "review_only_no_epub_mutation")
            self.assertEqual(result["openai_policy"], "label_assist_review_only_no_epub_mutation")
            self.assertEqual(result["openai_request_count"], 1)
            self.assertTrue((root / "queue" / "crops" / "scan_chess_p001_01.png").exists())
            self.assertTrue((root / "queue" / "openai_review_prompt.md").exists())
            request_path = root / "queue" / "openai_label_assist_requests.jsonl"
            self.assertTrue(request_path.exists())
            request = json.loads(request_path.read_text(encoding="utf-8").splitlines()[0])

        body = request["body"]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "/v1/responses")
        self.assertFalse(request["accepted_for_corpus"])
        self.assertEqual(request["review_policy"], "label_assist_review_only_no_epub_mutation")
        content = body["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_text")
        self.assertIn("review_only_no_epub_mutation", content[0]["text"])
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertIn("corrected_fen", body["text"]["format"]["schema"]["required"])

    def test_review_queue_flags_candidate_when_exported_crop_recognizes_different_position(self) -> None:
        import tempfile

        from scripts.build_chess_piece_templates import build_templates_from_labels
        from scripts.export_chess_fen_review_queue import export_chess_fen_review_queue

        actual_board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, _ = _labeled_board_png_and_templates(actual_board)
        actual_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1"
        stale_record_placement = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBKQBNR"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_path = root / "board.png"
            crop_path.write_bytes(image_data)
            labels_path = root / "labels.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "id": "synthetic_actual",
                        "crop_path": str(crop_path),
                        "fen": actual_fen,
                        "source_pdf": "synthetic",
                        "page": 1,
                        "diagram_index": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            template_dir = root / "templates"
            build_templates_from_labels(labels_path, output_dir=template_dir)

            epub_path = root / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("EPUB/images/scan_chess_p001_01.png", image_data)
            smoke_report = root / "smoke.json"
            smoke_report.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "output_epub": str(epub_path),
                                "quality_report": {
                                    "chess_fen": {
                                        "diagram_count": 1,
                                        "fen_count": 0,
                                        "records": [
                                            {
                                                "page": 1,
                                                "filename": "scan_chess_p001_01.png",
                                                "requires_review": True,
                                                "confidence": 0.83,
                                                "placement": stale_record_placement,
                                                "warnings": ["sparse_position_confidence_below_threshold"],
                                                "method": "image-template-board",
                                            }
                                        ],
                                    }
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = export_chess_fen_review_queue(
                smoke_report,
                output_dir=root / "queue",
                max_items=1,
                template_dir=template_dir,
                min_confidence=0.70,
            )

            item = result["queue"][0]
            self.assertEqual(item["review_crop_fen"], actual_fen)
            self.assertFalse(item["candidate_matches_review_crop"])
            self.assertIn("review_crop_candidate_mismatch", item["review_crop_warnings"])

    def test_image_template_result_exposes_square_confidence_matrix(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        image_data, templates = _labeled_board_png_and_templates(board)

        result = recognize_chess_position_from_image(image_data, piece_templates=templates, min_confidence=0.85)
        payload = result.to_dict()

        self.assertIn("squares", payload)
        self.assertEqual(len(payload["squares"]), 64)
        self.assertEqual(payload["squares"][0]["square"], "a8")
        self.assertEqual(payload["squares"][0]["piece"], "r")
        self.assertGreaterEqual(payload["squares"][0]["confidence"], 0.85)

    def test_image_template_rejects_text_square_without_board_pattern(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        _, templates = _labeled_board_png_and_templates(board)
        text_image = Image.new("L", (320, 320), 255)
        draw = ImageDraw.Draw(text_image)
        for index in range(18):
            draw.text((12, 10 + index * 16), f"{index + 1}. Qh5+ Nf6 2. Bb5 c6", fill=0)
        output = io.BytesIO()
        text_image.save(output, format="PNG")

        result = recognize_chess_position_from_image(
            output.getvalue(),
            piece_templates=templates,
            min_confidence=0.70,
        )

        self.assertEqual(result.fen, "")
        self.assertFalse(result.board_detected)
        self.assertIn("board_visual_pattern_not_detected", result.warnings)
        self.assertLess(_estimate_board_alternation_signal(text_image), 0.40)

    def test_board_signal_accepts_one_bit_review_crops(self) -> None:
        one_bit_board = Image.open(io.BytesIO(_board_png((240, 240)))).convert("1")

        signal = _estimate_board_alternation_signal(one_bit_board)
        result = recognize_chess_position_from_image(_image_bytes(one_bit_board))

        self.assertGreaterEqual(signal, 0.40)
        self.assertTrue(result.board_detected)
        self.assertIn("image_board_requires_review", result.warnings)

    def test_board_visual_gate_rejects_sparse_checker_like_text_noise(self) -> None:
        image = Image.new("L", (320, 320), 255)
        draw = ImageDraw.Draw(image)
        for row in range(8):
            for col in range(8):
                if (row + col) % 2:
                    cx = col * 40 + 20
                    cy = row * 40 + 20
                    draw.rectangle((cx - 1, cy - 1, cx + 1, cy + 1), fill=0)

        board_detected, score = _has_board_visual_pattern(image)

        self.assertGreaterEqual(_estimate_board_alternation_signal(image), 0.40)
        self.assertFalse(board_detected)
        self.assertLess(score, 0.40)

    def test_image_template_recognizer_ignores_board_coordinate_margin(self) -> None:
        board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        board_data, templates = _labeled_board_png_and_templates(board)
        board_image = Image.open(io.BytesIO(board_data)).convert("L").resize((320, 320), Image.Resampling.NEAREST)
        canvas = Image.new("L", (360, 360), 255)
        canvas.paste(board_image, (24, 8))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((21, 5, 346, 330), outline=0, width=3)
        for row, rank in enumerate("87654321"):
            draw.text((2, 17 + row * 40), rank, fill=0)
        for col, file_name in enumerate("abcdefgh"):
            draw.text((39 + col * 40, 336), file_name, fill=0)
        output = io.BytesIO()
        canvas.save(output, format="PNG")

        result = recognize_chess_position_from_image(output.getvalue(), piece_templates=templates, min_confidence=0.85)

        self.assertEqual(result.fen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1")

    def test_dense_board_area_crop_removes_captioned_margin(self) -> None:
        board_data = _board_png((240, 240))
        board_image = Image.open(io.BytesIO(board_data)).convert("L").resize((240, 240), Image.Resampling.NEAREST)
        canvas = Image.new("L", (300, 360), 255)
        canvas.paste(board_image, (30, 42))
        draw = ImageDraw.Draw(canvas)
        for row, rank in enumerate("87654321"):
            draw.text((8, 54 + row * 30), rank, fill=0)
        for col, file_name in enumerate("abcdefgh"):
            draw.text((43 + col * 30, 292), file_name, fill=0)
        draw.text((70, 326), "Example 7", fill=0)
        cropped = _dense_board_area_crop(canvas)

        self.assertIsNotNone(cropped)
        assert cropped is not None
        self.assertEqual(cropped.size[0], cropped.size[1])
        self.assertLess(cropped.size[0], canvas.size[1])

    def test_border_line_candidate_recovers_full_board_from_captioned_page(self) -> None:
        canvas = Image.new("L", (500, 620), 255)
        draw = ImageDraw.Draw(canvas)
        board_box = (260, 120, 420, 280)
        draw.rectangle(board_box, outline=0, width=3)
        for step in range(1, 8):
            x = board_box[0] + step * 20
            y = board_box[1] + step * 20
            draw.line((x, board_box[1], x, board_box[3]), fill=96, width=1)
            draw.line((board_box[0], y, board_box[2], y), fill=96, width=1)
        draw.text((282, 290), "a b c d e f g h", fill=0)
        draw.text((285, 326), "Example 2", fill=0)

        candidates = _border_line_square_candidates(canvas, scale=1.0)

        self.assertTrue(candidates)
        x0, y0, x1, y1 = candidates[0]
        self.assertLess(abs(x0 - 263), 3)
        self.assertLess(abs(y0 - 123), 3)
        self.assertLess(abs(x1 - 417), 3)
        self.assertLess(abs(y1 - 277), 3)

    def test_border_line_candidate_expands_near_square_box_to_square(self) -> None:
        canvas = Image.new("L", (520, 620), 255)
        draw = ImageDraw.Draw(canvas)
        # Real scans often detect the horizontal borders a little closer than
        # the vertical borders. Returning a rectangular bbox makes later
        # center-cropping drop the a/h files or edge ranks.
        board_box = (240, 140, 420, 300)
        draw.rectangle(board_box, outline=0, width=3)
        for step in range(1, 8):
            x = round(board_box[0] + step * ((board_box[2] - board_box[0]) / 8.0))
            y = round(board_box[1] + step * ((board_box[3] - board_box[1]) / 8.0))
            draw.line((x, board_box[1], x, board_box[3]), fill=96, width=1)
            draw.line((board_box[0], y, board_box[2], y), fill=96, width=1)

        candidates = _border_line_square_candidates(canvas, scale=1.0)

        self.assertTrue(candidates)
        x0, y0, x1, y1 = candidates[0]
        self.assertAlmostEqual(x1 - x0, y1 - y0, delta=1.5)
        self.assertGreaterEqual(x1 - x0, 175)
        self.assertLess(y0, board_box[1])

    def test_border_refinement_allows_only_local_crop_correction(self) -> None:
        original = (230.0, 2368.0, 932.0, 3060.0)
        nearby_border = (235.0, 2368.0, 937.0, 3074.0)
        shifted_partial_board = (1583.0, 2400.0, 2276.0, 3056.0)
        original_for_shift = (1569.0, 2040.0, 2280.0, 2825.0)
        shrink_crop = (240.0, 1699.0, 942.0, 2354.0)
        original_for_shrink = (154.0, 1539.0, 981.0, 2449.0)

        self.assertTrue(_border_refinement_is_local(original, nearby_border))
        self.assertFalse(_border_refinement_is_local(original_for_shift, shifted_partial_board))
        self.assertFalse(_border_refinement_is_local(original_for_shrink, shrink_crop))

    def test_dense_crop_fallback_requires_high_confidence(self) -> None:
        valid_board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        invalid_king_board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        invalid_king_board[0][4] = ""
        _, templates = _labeled_board_png_and_templates(valid_board)
        square_records = [{"piece": "", "confidence": 0.8, "row": 0, "col": 0}]
        image = Image.open(io.BytesIO(_board_png((320, 320)))).convert("L")

        with mock.patch(
            "chess_position_recognizer._classify_board_cells",
            side_effect=[
                (invalid_king_board, 0.80, square_records),
                (valid_board, 0.80, square_records),
            ],
        ), mock.patch(
            "chess_position_recognizer._dense_board_area_crop",
            return_value=image,
        ), mock.patch(
            "chess_position_recognizer._estimate_board_grid_confidence",
            return_value=0.80,
        ):
            result = _recognize_board_with_templates(
                image,
                templates,
                grid_confidence=0.80,
                bbox=None,
                min_confidence=0.70,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("black_king_count_invalid", result.warnings)

    def test_dense_crop_fallback_accepts_clear_improvement(self) -> None:
        valid_board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        invalid_king_board = [row[:] for row in valid_board]
        invalid_king_board[0][4] = ""
        _, templates = _labeled_board_png_and_templates(valid_board)
        square_records = [{"piece": "", "confidence": 0.8, "row": 0, "col": 0}]
        image = Image.open(io.BytesIO(_board_png((320, 320)))).convert("L")

        with mock.patch(
            "chess_position_recognizer._classify_board_cells",
            side_effect=[
                (invalid_king_board, 0.55, square_records),
                (valid_board, 0.80, square_records),
            ],
        ), mock.patch(
            "chess_position_recognizer._dense_board_area_crop",
            return_value=image,
        ), mock.patch(
            "chess_position_recognizer._estimate_board_grid_confidence",
            return_value=0.66,
        ):
            result = _recognize_board_with_templates(
                image,
                templates,
                grid_confidence=0.50,
                bbox=None,
                min_confidence=0.70,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.fen, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1")
        self.assertFalse(result.requires_review)
        self.assertIn("dense_board_area_crop_used", result.warnings)

    def test_dense_crop_fallback_rejects_non_board_crop(self) -> None:
        valid_board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        invalid_king_board = [
            row[:] for row in valid_board
        ]
        invalid_king_board[0][4] = ""
        _, templates = _labeled_board_png_and_templates(valid_board)
        square_records = [{"piece": "", "confidence": 0.98, "row": 0, "col": 0}]
        image = Image.new("L", (320, 320), 255)

        with mock.patch(
            "chess_position_recognizer._classify_board_cells",
            side_effect=[
                (invalid_king_board, 0.98, square_records),
                (valid_board, 0.98, square_records),
            ],
        ), mock.patch(
            "chess_position_recognizer._dense_board_area_crop",
            return_value=image,
        ), mock.patch(
            "chess_position_recognizer._estimate_board_grid_confidence",
            return_value=0.98,
        ):
            result = _recognize_board_with_templates(
                image,
                templates,
                grid_confidence=0.98,
                bbox=None,
                min_confidence=0.70,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("black_king_count_invalid", result.warnings)

    def test_low_grid_template_fen_needs_dense_board_evidence(self) -> None:
        valid_board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            [""] * 8,
            [""] * 8,
            [""] * 8,
            [""] * 8,
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        _, templates = _labeled_board_png_and_templates(valid_board)
        square_records = [{"piece": "", "confidence": 0.95, "row": 0, "col": 0}]
        image = Image.open(io.BytesIO(_board_png((320, 320)))).convert("L")

        with mock.patch(
            "chess_position_recognizer._classify_board_cells",
            return_value=(valid_board, 0.95, square_records),
        ), mock.patch(
            "chess_position_recognizer._dense_board_area_crop",
            return_value=None,
        ):
            result = _recognize_board_with_templates(
                image,
                templates,
                grid_confidence=0.50,
                bbox=None,
                min_confidence=0.70,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.fen, "")
        self.assertTrue(result.requires_review)
        self.assertIn("partial_board_crop_without_dense_board_evidence", result.warnings)

    def test_scan_chess_recognition_bbox_can_skip_reader_padding(self) -> None:
        raw_bbox = (100.0, 120.0, 260.0, 280.0)

        reader_bbox = _clamp_bbox(raw_bbox, (400, 400))
        recognition_bbox = _clamp_bbox(raw_bbox, (400, 400), pad_ratio=0.0, min_pad=0.0)

        self.assertEqual(reader_bbox, (96, 116, 264, 284))
        self.assertEqual(recognition_bbox, (100, 120, 260, 280))

    def test_scan_chess_candidate_ranking_prefers_recognized_fen(self) -> None:
        page = Image.new("RGB", (260, 140), "white")
        output = io.BytesIO()
        page.save(output, format="PNG")
        high_grid_invalid = ChessFenResult(
            confidence=0.92,
            bbox=(10.0, 10.0, 110.0, 110.0),
            method="high-grid",
            board_detected=True,
            requires_review=True,
        )
        lower_grid_valid = ChessFenResult(
            confidence=0.51,
            bbox=(140.0, 10.0, 240.0, 110.0),
            method="lower-grid",
            board_detected=True,
            requires_review=True,
        )
        invalid_recognition = ChessFenResult(
            placement="8/8/8/8/8/8/8/8",
            confidence=0.88,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        valid_recognition = ChessFenResult(
            fen="8/8/8/8/8/8/7k/7K w - - 0 1",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.72,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "pymupdf_chess_extractor._scan_chess_recognition_cache_path",
            side_effect=lambda *_args, **kwargs: Path(temp_dir)
            / ("recognition_" + "_".join(str(int(value)) for value in kwargs.get("bbox", (0, 0, 0, 0))) + ".json"),
        ), mock.patch(
            "pymupdf_chess_extractor._scan_chess_legacy_recognition_cache_paths",
            return_value=[],
        ), mock.patch(
            "pymupdf_chess_extractor.recognize_chess_position_from_image",
            side_effect=[
                invalid_recognition,
                valid_recognition,
            ],
        ):
            ranked = _rank_scan_chess_page_candidates_by_recognition(
                output.getvalue(),
                [high_grid_invalid, lower_grid_valid],
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
            )

        self.assertEqual([candidate.method for candidate in ranked], ["lower-grid", "high-grid"])

    def test_scan_chess_candidate_recognition_uses_crop_cache(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((20, 20, 180, 180), outline="black", width=3)
        recognition = ChessFenResult(
            fen="8/8/8/8/8/8/7k/7K w - - 0 1",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.91,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch(
            "pymupdf_chess_extractor._scan_chess_recognition_cache_path",
            side_effect=lambda *_args, **_kwargs: Path(temp_dir) / "recognition.json",
        ), mock.patch(
            "pymupdf_chess_extractor._scan_chess_legacy_recognition_cache_paths",
            return_value=[],
        ), mock.patch(
            "pymupdf_chess_extractor.recognize_chess_position_from_image",
            return_value=recognition,
        ) as recognize_mock:
            first = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )
            second = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(first.fen, recognition.fen)
        self.assertEqual(second.fen, recognition.fen)
        self.assertEqual(recognize_mock.call_count, 1)

    def test_scan_chess_candidate_recognition_skips_reader_probe_for_high_confidence_raw_fen(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        recognition = ChessFenResult(
            fen="8/8/8/8/8/8/7k/7K w - - 0 1",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.93,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=recognition,
        ) as recognize_mock:
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(result.fen, recognition.fen)
        self.assertEqual(recognize_mock.call_count, 1)

    def test_scan_chess_candidate_recognition_uses_reader_visible_padded_crop(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        raw_review = ChessFenResult(
            placement="8/8/8/8/8/8/7k/8",
            confidence=0.83,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        reader_fen = ChessFenResult(
            fen="8/8/8/8/8/8/7k/7K w - - 0 1",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.84,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )
        seen_bboxes = []

        def fake_recognize(_crop_bytes, *, bbox, **_kwargs):
            seen_bboxes.append(tuple(float(value) for value in bbox))
            if tuple(float(value) for value in bbox) == (16.0, 16.0, 184.0, 184.0):
                return reader_fen
            return raw_review

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            side_effect=fake_recognize,
        ):
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(result.fen, reader_fen.fen)
        self.assertIn("reader_visible_crop_fen_used", result.warnings)
        self.assertIn((20.0, 20.0, 180.0, 180.0), seen_bboxes)
        self.assertIn((16.0, 16.0, 184.0, 184.0), seen_bboxes)

    def test_scan_chess_candidate_recognition_uses_explicit_reader_bbox(self) -> None:
        page = Image.new("RGB", (240, 240), "white")
        raw_review = ChessFenResult(
            fen="8/8/8/8/1k6/7K/7P/8 w - - 0 1",
            placement="8/8/8/8/1k6/7K/7P/8",
            confidence=0.832,
            method="image-template-board",
            warnings=["sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )
        reader_fen = ChessFenResult(
            fen="8/8/8/8/1k6/7K/7P/8 w - - 0 1",
            placement="8/8/8/8/1k6/7K/7P/8",
            confidence=0.869,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )
        seen_bboxes = []

        def fake_recognize(_crop_bytes, *, bbox, **_kwargs):
            rounded = tuple(float(value) for value in bbox)
            seen_bboxes.append(rounded)
            if rounded == (8.0, 18.0, 198.0, 208.0):
                return reader_fen
            return raw_review

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            side_effect=fake_recognize,
        ):
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
                reader_bbox=(8.0, 18.0, 198.0, 208.0),
            )

        self.assertEqual(result.fen, reader_fen.fen)
        self.assertIn("reader_visible_crop_fen_used", result.warnings)
        self.assertIn((20.0, 20.0, 180.0, 180.0), seen_bboxes)
        self.assertIn((8.0, 18.0, 198.0, 208.0), seen_bboxes)
        self.assertNotIn((16.0, 16.0, 184.0, 184.0), seen_bboxes)

    def test_scan_chess_final_rendered_crop_confirms_visible_fen(self) -> None:
        raw_review = ChessFenResult(
            placement="8/8/8/8/1p2k3/8/b1P5/8",
            confidence=0.825,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        final_fen = ChessFenResult(
            fen="8/8/8/8/1p2k3/8/b1PK4/8 w - - 0 1",
            placement="8/8/8/8/1p2k3/8/b1PK4/8",
            confidence=0.838,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=final_fen,
        ) as recognize_mock:
            result = _scan_chess_confirm_final_rendered_crop_recognition(
                raw_review,
                b"final-png-bytes",
                bbox=(10.0, 20.0, 190.0, 200.0),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(result.fen, final_fen.fen)
        self.assertIn("final_rendered_crop_fen_used", result.warnings)
        recognize_mock.assert_called_once()

    def test_scan_chess_final_crop_accepts_sparse_superset_without_conflict(self) -> None:
        raw_review = ChessFenResult(
            placement="8/8/8/8/1k6/7K/8/8",
            confidence=0.832,
            method="image-template-board",
            warnings=["sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )
        final_fen = ChessFenResult(
            fen="8/8/8/8/1k6/7K/7P/8 w - - 0 1",
            placement="8/8/8/8/1k6/7K/7P/8",
            confidence=0.869,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=final_fen,
        ):
            result = _scan_chess_confirm_final_rendered_crop_recognition(
                raw_review,
                b"final-png-bytes",
                bbox=(10.0, 20.0, 190.0, 200.0),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(result.fen, final_fen.fen)
        self.assertIn("final_rendered_crop_fen_used", result.warnings)

    def test_scan_chess_final_crop_rejects_sparse_conflicting_position(self) -> None:
        raw_review = ChessFenResult(
            placement="8/8/8/3qK3/8/2k5/2p5/8",
            confidence=0.811,
            method="image-template-board",
            warnings=["sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )
        final_conflict = ChessFenResult(
            fen="8/8/8/3QK3/8/2k5/2p5/8 w - - 0 1",
            placement="8/8/8/3QK3/8/2k5/2p5/8",
            confidence=0.839,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=final_conflict,
        ):
            result = _scan_chess_confirm_final_rendered_crop_recognition(
                raw_review,
                b"final-png-bytes",
                bbox=(10.0, 20.0, 190.0, 200.0),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertIs(result, raw_review)

    def test_scan_chess_final_crop_accepts_exact_sparse_consensus(self) -> None:
        placement = "2K4Q/8/2N5/1k6/3B4/8/P5P1/8"
        raw_review = ChessFenResult(
            fen=f"{placement} w - - 0 1",
            placement=placement,
            confidence=0.812,
            side_to_move="w",
            method="image-template-board",
            warnings=["side_to_move_marker_detected", "sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )
        final_sparse_review = ChessFenResult(
            fen="",
            placement=placement,
            confidence=0.828,
            side_to_move="w",
            method="image-template-board",
            warnings=["side_to_move_inferred", "sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=final_sparse_review,
        ):
            result = _scan_chess_confirm_final_rendered_crop_recognition(
                raw_review,
                b"final-png-bytes",
                bbox=(10.0, 20.0, 190.0, 200.0),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertFalse(result.requires_review)
        self.assertEqual(result.fen, f"{placement} w - - 0 1")
        self.assertIn("sparse_exact_crop_consensus", result.warnings)
        self.assertIn("final_rendered_crop_sparse_consensus_fen_used", result.warnings)

    def test_scan_chess_final_crop_rejects_exact_sparse_consensus_below_floor(self) -> None:
        placement = "2K4Q/8/2N5/1k6/3B4/8/P5P1/8"
        raw_review = ChessFenResult(
            fen=f"{placement} w - - 0 1",
            placement=placement,
            confidence=0.812,
            side_to_move="w",
            method="image-template-board",
            warnings=["sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )
        final_sparse_review = ChessFenResult(
            fen="",
            placement=placement,
            confidence=0.826,
            side_to_move="w",
            method="image-template-board",
            warnings=["side_to_move_inferred", "sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=final_sparse_review,
        ):
            result = _scan_chess_confirm_final_rendered_crop_recognition(
                raw_review,
                b"final-png-bytes",
                bbox=(10.0, 20.0, 190.0, 200.0),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertIs(result, raw_review)

    def test_scan_chess_verified_exact_crop_label_overrides_review_only_result(self) -> None:
        crop_bytes = b"verified-crop-png"
        digest = __import__("hashlib").sha256(crop_bytes).hexdigest()
        fen = "8/1k6/1p6/1K6/P1P5/8/8/8 w - - 0 1"
        review_result = ChessFenResult(
            fen="",
            placement="8/1q6/1p6/1K6/P1P5/8/8/8",
            confidence=0.832,
            method="image-template-board",
            warnings=["black_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "verified.jsonl"
            labels_path.write_text(json.dumps({"sha256": digest, "fen": fen}) + "\n", encoding="utf-8")
            result = _scan_chess_apply_verified_crop_label(
                review_result,
                crop_bytes,
                bbox=(1.0, 2.0, 100.0, 101.0),
                config=ConversionConfig(chess_fen_verified_crop_labels_path=str(labels_path)),
            )

        self.assertFalse(result.requires_review)
        self.assertEqual(result.fen, fen)
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.method, "verified-exact-crop-label")
        self.assertIn("verified_exact_crop_label_used", result.warnings)
        self.assertNotIn("black_king_count_invalid", result.warnings)

    def test_scan_chess_verified_exact_crop_label_requires_hash_match(self) -> None:
        review_result = ChessFenResult(
            fen="",
            placement="8/1q6/1p6/1K6/P1P5/8/8/8",
            confidence=0.832,
            method="image-template-board",
            warnings=["black_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "verified.jsonl"
            labels_path.write_text(
                json.dumps(
                    {
                        "sha256": __import__("hashlib").sha256(b"other-crop").hexdigest(),
                        "fen": "8/1k6/1p6/1K6/P1P5/8/8/8 w - - 0 1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = _scan_chess_apply_verified_crop_label(
                review_result,
                b"unmatched-crop",
                bbox=(1.0, 2.0, 100.0, 101.0),
                config=ConversionConfig(chess_fen_verified_crop_labels_path=str(labels_path)),
            )

        self.assertIs(result, review_result)

    def test_scan_chess_candidate_recognition_keeps_sparse_review_without_king_failure(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        sparse_review = ChessFenResult(
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.83,
            method="image-template-board",
            warnings=["sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            return_value=sparse_review,
        ) as recognize_mock:
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertIs(result, sparse_review)
        self.assertEqual(recognize_mock.call_count, 2)

    def test_scan_chess_reader_visible_crop_accepts_matching_sparse_position(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        placement = "8/8/8/8/1k6/7K/7P/8"
        raw_sparse_review = ChessFenResult(
            fen=f"{placement} w - - 0 1",
            placement=placement,
            confidence=0.79,
            method="image-template-board",
            warnings=["sparse_position_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )
        reader_fen = ChessFenResult(
            fen=f"{placement} w - - 0 1",
            placement=placement,
            confidence=0.86,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        def fake_recognize(_crop_bytes, *, bbox, **_kwargs):
            if tuple(float(value) for value in bbox) == (16.0, 16.0, 184.0, 184.0):
                return reader_fen
            return raw_sparse_review

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            side_effect=fake_recognize,
        ):
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(result.fen, reader_fen.fen)
        self.assertIn("reader_visible_crop_fen_used", result.warnings)

    def test_scan_chess_reader_visible_crop_can_replace_clipped_raw_position(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        raw_review = ChessFenResult(
            placement="8/8/8/8/8/8/7k/8",
            confidence=0.83,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        mutated_reader_fen = ChessFenResult(
            fen="8/8/8/8/8/8/6Pk/7K w - - 0 1",
            placement="8/8/8/8/8/8/6Pk/7K",
            confidence=0.86,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        def fake_recognize(_crop_bytes, *, bbox, **_kwargs):
            if tuple(float(value) for value in bbox) == (16.0, 16.0, 184.0, 184.0):
                return mutated_reader_fen
            return raw_review

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            side_effect=fake_recognize,
        ):
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertEqual(result.fen, mutated_reader_fen.fen)
        self.assertIn("reader_visible_crop_fen_used", result.warnings)

    def test_scan_chess_reader_visible_crop_rejects_low_confidence_replacement(self) -> None:
        page = Image.new("RGB", (220, 220), "white")
        raw_review = ChessFenResult(
            placement="8/8/8/8/8/8/7k/8",
            confidence=0.83,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        low_confidence_reader_fen = ChessFenResult(
            fen="8/8/8/8/8/8/6Pk/7K w - - 0 1",
            placement="8/8/8/8/8/8/6Pk/7K",
            confidence=0.79,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        def fake_recognize(_crop_bytes, *, bbox, **_kwargs):
            if tuple(float(value) for value in bbox) == (16.0, 16.0, 184.0, 184.0):
                return low_confidence_reader_fen
            return raw_review

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_crop_with_cache",
            side_effect=fake_recognize,
        ):
            result = _recognize_scan_chess_candidate_bbox(
                page,
                (20.0, 20.0, 180.0, 180.0),
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertIs(result, raw_review)

    def test_scan_chess_effective_candidate_limit_preserves_two_by_three_exercise_pages(self) -> None:
        candidates = []
        for row, y in enumerate((100.0, 360.0, 620.0)):
            for col, x in enumerate((100.0, 420.0)):
                candidates.append(
                    ChessFenResult(
                        confidence=0.90 - (row * 2 + col) * 0.01,
                        bbox=(x, y, x + 220.0, y + 220.0),
                        method="image-page-board-candidate",
                        board_detected=True,
                        requires_review=True,
                    )
                )

        self.assertEqual(_scan_chess_effective_page_candidate_limit(candidates, 6), 6)
        self.assertEqual(_scan_chess_recognition_pool_size(6, max_candidates=6), 10)

    def test_scan_chess_effective_candidate_limit_preserves_two_by_two_pages(self) -> None:
        candidates = []
        for y in (100.0, 360.0):
            for x in (100.0, 420.0):
                candidates.append(
                    ChessFenResult(
                        confidence=0.90,
                        bbox=(x, y, x + 220.0, y + 220.0),
                        method="image-page-board-candidate",
                        board_detected=True,
                        requires_review=True,
                    )
                )

        self.assertEqual(_scan_chess_effective_page_candidate_limit(candidates, 6), 4)

    def test_scan_chess_effective_candidate_limit_keeps_regular_pages_fast(self) -> None:
        candidates = [
            ChessFenResult(
                confidence=0.90 - i * 0.02,
                bbox=(80.0 + i * 30.0, 120.0 + i * 22.0, 300.0 + i * 30.0, 340.0 + i * 22.0),
                method="image-page-board-candidate",
                board_detected=True,
                requires_review=True,
            )
            for i in range(8)
        ]

        self.assertEqual(_scan_chess_effective_page_candidate_limit(candidates, 6), 3)
        self.assertEqual(_scan_chess_recognition_pool_size(3, max_candidates=6), 4)

    def test_scan_chess_page_candidates_passes_small_regular_pool_to_ranker(self) -> None:
        class FakeDoc:
            def __len__(self) -> int:
                return 1

        candidates = [
            ChessFenResult(
                confidence=0.90 - i * 0.02,
                bbox=(80.0 + i * 30.0, 120.0 + i * 22.0, 300.0 + i * 30.0, 340.0 + i * 22.0),
                method="image-page-board-candidate",
                board_detected=True,
                requires_review=True,
            )
            for i in range(8)
        ]
        ranked_pool_sizes = []

        def fake_rank(_image_data, ranked_candidates, **_kwargs):
            ranked_pool_sizes.append(len(ranked_candidates))
            return ranked_candidates

        with mock.patch(
            "pymupdf_chess_extractor._page_image_data_for_scan_chess",
            return_value=b"page",
        ), mock.patch(
            "pymupdf_chess_extractor.detect_board_candidates_in_page_image",
            return_value=candidates,
        ), mock.patch(
            "pymupdf_chess_extractor._rank_scan_chess_page_candidates_by_recognition",
            side_effect=fake_rank,
        ):
            pages = _scan_chess_page_candidates(
                "dummy.pdf",
                FakeDoc(),
                ConversionConfig(
                    scanned_chess_max_pages=1,
                    scanned_chess_cache_enabled=False,
                    chess_fen_scan_candidates_per_page=6,
                ),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
            )

        self.assertEqual(ranked_pool_sizes, [4])
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]["candidates"]), 3)

    def test_scan_chess_current_geometry_recovers_top_caption_board_crops(self) -> None:
        import fitz
        from pymupdf_chess_extractor import _page_image_data_for_scan_chess

        pdf_path = Path("reference_inputs/pdf/fundamenty_1_1_scan_chess.pdf")
        template_dir = Path("reference_inputs/chess_fen/templates/fundamenty_merida_like")
        if not pdf_path.exists() or not template_dir.exists():
            self.skipTest("Fundamenty scan fixture or templates are unavailable")

        config = ConversionConfig(
            chess_fen_min_confidence=0.70,
            chess_fen_piece_template_dir=str(template_dir),
        )
        piece_templates = load_piece_templates(template_dir)
        expected_pages = {
            168: {
                "8/r4KP1/6Pk/8/8/8/8/8",
                "6K1/3nkP2/8/5P2/8/2b5/8/8",
            },
            195: {
                "r3r3/pp3pk1/3p1bp1/2pP1q2/2P2P2/1PN2bPp/P4b1P/2R1RQK1",
            },
        }

        doc = fitz.open(pdf_path)
        try:
            max_page_num = max(expected_pages)
            if doc.page_count <= max_page_num:
                self.skipTest("Full Fundamenty scan fixture is unavailable in this environment")
            for page_num, expected_placements in expected_pages.items():
                image_data = _page_image_data_for_scan_chess(doc, page_num)
                candidates = detect_board_candidates_in_page_image(
                    image_data,
                    max_candidates=10,
                    min_grid_confidence=0.50,
                    enable_sliding_probe=False,
                )
                ranked = _rank_scan_chess_page_candidates_by_recognition(
                    image_data,
                    candidates,
                    config=config,
                    piece_templates=piece_templates,
                )
                placements = {str(candidate.bbox) for candidate in ranked}
                self.assertGreaterEqual(len(ranked), len(expected_placements), placements)

                from PIL import Image as PILImage

                page_image = PILImage.open(io.BytesIO(image_data)).convert("RGB")
                recognized = set()
                review_warnings: list[str] = []
                for candidate in ranked:
                    if not candidate.bbox:
                        continue
                    result = _recognize_scan_chess_candidate_bbox(
                        page_image,
                        tuple(float(value) for value in candidate.bbox),
                        config=config,
                        piece_templates=piece_templates,
                        min_confidence=0.70,
                    )
                    if result.fen:
                        recognized.add(result.placement)
                    else:
                        review_warnings.extend(result.warnings)

                self.assertTrue(
                    expected_placements.issubset(recognized),
                    f"page={page_num + 1}, recognized={sorted(recognized)}, warnings={review_warnings}",
                )
        finally:
            doc.close()

    def test_scan_chess_page_candidates_resumes_partial_cache(self) -> None:
        class FakeDoc:
            def __len__(self) -> int:
                return 2

        candidate = ChessFenResult(
            confidence=0.90,
            bbox=(100.0, 100.0, 260.0, 260.0),
            method="image-page-board-candidate",
            board_detected=True,
            requires_review=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "scan-cache.json"
            cache_key = {
                "version": SCAN_CHESS_PAGE_CANDIDATE_CACHE_VERSION,
                "page_limit": 2,
                "max_candidates": 6,
                "min_grid_confidence": 0.5,
                "template_token": "",
            }
            cache_path.write_text(
                json.dumps(
                    {
                        "cache_key": cache_key,
                        "complete": False,
                        "processed_page_nums": [0],
                        "pages": [],
                    }
                ),
                encoding="utf-8",
            )
            processed_pages = []

            def fake_page_image(_doc, page_num):
                processed_pages.append(page_num)
                return b"page"

            with mock.patch(
                "pymupdf_chess_extractor._scan_chess_cache_path",
                return_value=cache_path,
            ), mock.patch(
                "pymupdf_chess_extractor._page_image_data_for_scan_chess",
                side_effect=fake_page_image,
            ), mock.patch(
                "pymupdf_chess_extractor.detect_board_candidates_in_page_image",
                return_value=[candidate],
            ):
                pages = _scan_chess_page_candidates(
                    "dummy.pdf",
                    FakeDoc(),
                    ConversionConfig(
                        scanned_chess_max_pages=2,
                        scanned_chess_cache_enabled=True,
                        chess_fen_scan_candidates_per_page=6,
                    ),
                    piece_templates=None,
                )

            cached = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(processed_pages, [1])
        self.assertTrue(cached["complete"])
        self.assertEqual(cached["processed_page_nums"], [0, 1])
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["page_num"], 1)

    def test_scan_chess_ranking_drops_unrecognized_border_recovery_candidate(self) -> None:
        page = Image.new("RGB", (280, 140), "white")
        output = io.BytesIO()
        page.save(output, format="PNG")
        border_recovery = ChessFenResult(
            confidence=0.50,
            bbox=(10.0, 10.0, 110.0, 110.0),
            method="image-page-board-border",
            board_detected=True,
            requires_review=True,
        )
        regular_review = ChessFenResult(
            confidence=0.49,
            bbox=(150.0, 10.0, 250.0, 110.0),
            method="image-page-board-candidate",
            board_detected=True,
            requires_review=True,
        )
        invalid_recognition = ChessFenResult(
            placement="8/8/8/8/8/8/8/8",
            confidence=0.68,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor.recognize_chess_position_from_image",
            side_effect=[
                invalid_recognition,
                invalid_recognition,
                invalid_recognition,
                invalid_recognition,
            ],
        ):
            ranked = _rank_scan_chess_page_candidates_by_recognition(
                output.getvalue(),
                [border_recovery, regular_review],
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
            )

        self.assertEqual([candidate.method for candidate in ranked], ["image-page-board-candidate"])

    def test_scan_chess_vertical_recovery_generates_local_shifted_board_boxes(self) -> None:
        variants = _scan_chess_vertical_recovery_bboxes((100.0, 260.0, 260.0, 418.0), (500, 600))

        self.assertIn((101.5, 220.0, 261.5, 380.0), variants)
        self.assertTrue(all(top < 260.0 for _, top, _, _ in variants))
        self.assertTrue(all(0.0 <= left < right <= 500.0 for left, _, right, _ in variants))

    def test_scan_chess_candidate_ranking_promotes_shift_recovered_fen_bbox(self) -> None:
        page = Image.new("RGB", (360, 360), "white")
        output = io.BytesIO()
        page.save(output, format="PNG")
        shifted_partial = ChessFenResult(
            confidence=0.53,
            bbox=(80.0, 180.0, 240.0, 338.0),
            method="image-page-board-candidate",
            board_detected=True,
            requires_review=True,
        )
        invalid_recognition = ChessFenResult(
            placement="2p1p3/5p2/8/8/4B3/8/8/8",
            confidence=0.64,
            method="image-template-board",
            warnings=["black_king_count_invalid", "white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        recovered_recognition = ChessFenResult(
            fen="q3r3/1p1r1kpp/2pPp3/2P2p2/PR1P4/4R1P1/4Q1KP/8 w - - 0 1",
            placement="q3r3/1p1r1kpp/2pPp3/2P2p2/PR1P4/4R1P1/4Q1KP/8",
            confidence=0.91,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        def fake_recognize(_page_image, bbox, **_kwargs):
            if bbox[1] < shifted_partial.bbox[1] and bbox[0] > shifted_partial.bbox[0]:
                return recovered_recognition
            return invalid_recognition

        with mock.patch("pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox", side_effect=fake_recognize):
            ranked = _rank_scan_chess_page_candidates_by_recognition(
                output.getvalue(),
                [shifted_partial],
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
            )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].method, "image-page-board-shift-recovered")
        self.assertLess(ranked[0].bbox[1], shifted_partial.bbox[1])
        self.assertFalse(ranked[0].requires_review)

    def test_scan_chess_ranking_does_not_shift_border_refined_candidates(self) -> None:
        page = Image.new("RGB", (360, 360), "white")
        output = io.BytesIO()
        page.save(output, format="PNG")
        border_refined = ChessFenResult(
            confidence=0.53,
            bbox=(80.0, 180.0, 240.0, 338.0),
            method="image-page-board-border-refined",
            board_detected=True,
            requires_review=True,
        )
        invalid_recognition = ChessFenResult(
            placement="8/8/8/r3r3/pp3pk1/3p1bp1/2pP1q2/2P2P2",
            confidence=0.83,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        recovered_recognition = ChessFenResult(
            fen="8/2K5/r3r3/pp3pk1/3p1bp1/2pP1q2/2P2P2/1PN2bPp w - - 0 1",
            placement="8/2K5/r3r3/pp3pk1/3p1bp1/2pP1q2/2P2P2/1PN2bPp",
            confidence=0.91,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox",
            return_value=invalid_recognition,
        ), mock.patch(
            "pymupdf_chess_extractor._recover_shifted_scan_chess_candidate",
            return_value=(border_refined, recovered_recognition),
        ) as recover_mock:
            ranked = _rank_scan_chess_page_candidates_by_recognition(
                output.getvalue(),
                [border_refined],
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
            )

        recover_mock.assert_not_called()
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].method, "image-page-board-border-refined")
        self.assertTrue(ranked[0].requires_review)

    def test_scan_chess_ranking_skips_shift_recovery_on_multi_diagram_grid(self) -> None:
        page = Image.new("RGB", (900, 900), "white")
        output = io.BytesIO()
        page.save(output, format="PNG")
        candidates = []
        for row, y in enumerate((80.0, 340.0, 600.0)):
            for col, x in enumerate((80.0, 420.0)):
                candidates.append(
                    ChessFenResult(
                        confidence=0.80 - (row * 2 + col) * 0.01,
                        bbox=(x, y, x + 220.0, y + 220.0),
                        method="image-page-board-candidate",
                        board_detected=True,
                        requires_review=True,
                    )
                )
        invalid_recognition = ChessFenResult(
            placement="8/8/8/8/8/8/8/8",
            confidence=0.72,
            method="image-template-board",
            warnings=["black_king_count_invalid", "white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox",
            return_value=invalid_recognition,
        ), mock.patch(
            "pymupdf_chess_extractor._recover_shifted_scan_chess_candidate",
            return_value=None,
        ) as recover_mock:
            ranked = _rank_scan_chess_page_candidates_by_recognition(
                output.getvalue(),
                candidates,
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
            )

        recover_mock.assert_not_called()
        self.assertEqual(len(ranked), 6)
        self.assertTrue(all(candidate.method == "image-page-board-candidate" for candidate in ranked))

    def test_scan_chess_local_expansion_bboxes_keep_right_edge_stable(self) -> None:
        variants = _scan_chess_local_expansion_bboxes(
            (1569.4, 1250.9, 2275.6, 1957.1),
            (2480, 3508),
        )

        self.assertTrue(variants)
        self.assertTrue(all(abs(right - 2275.6) < 0.001 for _, _, right, _ in variants))
        self.assertTrue(any(abs(left - 1550.7) < 0.2 and abs(top - 1244.7) < 0.3 for left, top, _, _ in variants))

    def test_scan_chess_expanded_recovery_accepts_only_dense_full_boards(self) -> None:
        page = Image.new("RGB", (2480, 3508), "white")
        border_refined = ChessFenResult(
            confidence=0.53,
            bbox=(1569.4, 1250.9, 2275.6, 1957.1),
            method="image-page-board-border-refined",
            board_detected=True,
            requires_review=True,
        )
        invalid_recognition = ChessFenResult(
            placement="p3r3/pp3pk1/3p1bp1/2p2Q2/8/5b1p/8/5Q2",
            confidence=0.64,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        sparse_false_positive = ChessFenResult(
            fen="8/8/8/8/8/2k5/8/3K4 w - - 0 1",
            placement="8/8/8/8/8/2k5/8/3K4",
            confidence=0.93,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )
        recovered_recognition = ChessFenResult(
            fen="r3r3/pp3pk1/3p1bp1/2pP1q2/2P2P2/1PN2bPp/P4b1P/2R1RQK1 w - - 0 1",
            placement="r3r3/pp3pk1/3p1bp1/2pP1q2/2P2P2/1PN2bPp/P4b1P/2R1RQK1",
            confidence=0.91,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        def fake_recognize(_page_image, bbox, **_kwargs):
            left, top, _, _ = bbox
            if abs(left - 1550.7) < 0.2 and abs(top - 1244.7) < 0.3:
                return recovered_recognition
            if top < border_refined.bbox[1]:
                return sparse_false_positive
            return invalid_recognition

        with mock.patch("pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox", side_effect=fake_recognize):
            recovered = _recover_expanded_scan_chess_candidate(
                page,
                border_refined,
                config=ConversionConfig(chess_fen_min_confidence=0.70),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                min_confidence=0.70,
            )

        self.assertIsNotNone(recovered)
        assert recovered is not None
        recovered_candidate, recognition = recovered
        self.assertEqual(recovered_candidate.method, "image-page-board-border-expanded")
        self.assertFalse(recovered_candidate.requires_review)
        self.assertEqual(recognition.fen, recovered_recognition.fen)

    def test_scan_chess_prefers_reader_prepared_fen_over_raw_review(self) -> None:
        raw_review = ChessFenResult(
            placement="8/8/8/8/8/8/8/8",
            confidence=0.90,
            method="image-template-board",
            warnings=["white_king_count_invalid"],
            requires_review=True,
            board_detected=True,
        )
        reader_fen = ChessFenResult(
            fen="8/8/8/8/8/8/7k/7K w - - 0 1",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.72,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        preferred = _prefer_scan_chess_recognition_result(raw_review, reader_fen)

        self.assertIs(preferred, reader_fen)

    def test_openai_chess_review_payload_never_changes_fen_output(self) -> None:
        from chess_position_recognizer import ChessFenResult, review_chess_fen_candidate

        class FakeProvider:
            name = "fake-openai"

            def review_chess_fen(self, context):
                return {"status": "reviewed", "suggested_fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1"}

        result = ChessFenResult(fen="", confidence=0.2, requires_review=True, board_detected=True)
        review = review_chess_fen_candidate(result, provider=FakeProvider(), context={"source": "unit"})

        self.assertEqual(review["status"], "reviewed")
        self.assertFalse(review["changed_output"])
        self.assertEqual(result.fen, "")

    def test_openai_chess_reviewer_payload_is_review_only_audit_data(self) -> None:
        from openai_chess_fen_reviewer import OpenAIChessFenReviewer, openai_chess_fen_reviewer_status

        with mock.patch.dict(os.environ, {}, clear=True):
            status = openai_chess_fen_reviewer_status(env={"OPENAI_API_KEY": ""})

        self.assertFalse(status["enabled"])
        self.assertFalse(status["api_key_present"])
        self.assertEqual(status["mode"], "review_only")
        self.assertFalse(status["mutates_fen"])

        review = OpenAIChessFenReviewer(model="unit-model").review_chess_fen(
            {"candidate": {"fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1"}}
        )

        self.assertEqual(review["status"], "reviewed")
        self.assertEqual(review["mode"], "review_only")
        self.assertFalse(review["mutates_fen"])
        self.assertFalse(review["changed_output"])
        self.assertEqual(review["candidate_fen"], "8/8/8/3k4/8/8/4K3/8 w - - 0 1")
        self.assertEqual(review["suggested_label"], "")
        self.assertIn("live_openai_review_not_configured", review["issues"])

    def test_openai_chess_status_checker_writes_audit_output_file(self) -> None:
        import sys

        from scripts.check_openai_chess_fen_reviewer import main

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "openai_status.json"
            with mock.patch.object(sys, "argv", ["check_openai_chess_fen_reviewer.py", "--output", str(output)]):
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(main(), 0)

            status = json.loads(output.read_text(encoding="utf-8"))

        self.assertFalse(status["configured"])
        self.assertEqual(status["mode"], "review_only")
        self.assertFalse(status["mutates_fen"])

    def test_openai_chess_reviewer_builds_from_env_but_stays_review_only(self) -> None:
        from openai_chess_fen_reviewer import build_openai_chess_fen_reviewer_from_env, openai_chess_fen_reviewer_status

        env = {
            "KINDLEMASTER_OPENAI_CHESS_FEN_REVIEW": "1",
            "OPENAI_API_KEY": "sk-test",
            "KINDLEMASTER_OPENAI_CHESS_FEN_MODEL": "unit-vision-model",
            "OPENAI_BASE_URL": "https://example.test/v1",
        }

        provider = build_openai_chess_fen_reviewer_from_env(env=env)
        status = openai_chess_fen_reviewer_status(env=env)

        self.assertIsNotNone(provider)
        assert provider is not None
        self.assertEqual(provider.model, "unit-vision-model")
        self.assertEqual(provider.base_url, "https://example.test/v1")
        self.assertTrue(status["enabled"])
        self.assertTrue(status["api_key_present"])
        self.assertEqual(status["mode"], "review_only")
        self.assertFalse(status["mutates_fen"])
        self.assertFalse(status["full_document_upload"])

    def test_openai_chess_live_review_uses_image_payload_without_mutating_fen(self) -> None:
        from chess_position_recognizer import review_chess_fen_candidate
        from openai_chess_fen_reviewer import OpenAIChessFenReviewer

        captured: dict[str, object] = {}

        def fake_transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            return {
                "id": "resp_unit",
                "output_text": json.dumps(
                    {
                        "approved": True,
                        "corrected_fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                        "requires_review": False,
                        "ambiguous_squares": [],
                        "issues": [],
                        "confidence": 0.91,
                        "notes": "unit review only",
                    }
                ),
                "usage": {"input_tokens": 10, "output_tokens": 8},
            }

        provider = OpenAIChessFenReviewer(
            model="unit-vision-model",
            api_key="sk-test",
            base_url="https://example.test/v1",
            transport=fake_transport,
        )
        result = ChessFenResult(
            fen="",
            placement="8/8/8/3k4/8/8/4K3/8",
            confidence=0.73,
            requires_review=True,
            warnings=["confidence_below_threshold"],
            board_detected=True,
        )

        review = review_chess_fen_candidate(
            result,
            provider=provider,
            image_data=b"\x89PNG\r\n\x1a\nunit",
            context={"source": "unit", "page": 7},
        )

        payload = captured["payload"]
        assert isinstance(payload, dict)
        content = payload["input"][0]["content"]
        self.assertEqual(captured["url"], "https://example.test/v1/responses")
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(review["status"], "reviewed")
        self.assertTrue(review["approved"])
        self.assertEqual(review["suggested_label"], "8/8/8/3k4/8/8/4K3/8 w - - 0 1")
        self.assertFalse(review["changed_output"])
        self.assertEqual(result.fen, "")

    def test_openai_chess_repair_proposer_uses_image_schema_and_reasoning_effort(self) -> None:
        from openai_chess_repair_proposer import OpenAIChessRepairProposer, openai_chess_repair_proposer_status

        captured: dict[str, object] = {}

        def fake_transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            return {
                "id": "resp_repair_unit",
                "output_text": json.dumps(
                    {
                        "fen_candidates": [
                            {
                                "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                                "confidence": 0.91,
                                "notes": "unit fen",
                            }
                        ],
                        "solution_line_candidates": [
                            {"movetext": "1. Kd3", "confidence": 0.82, "notes": "unit line"}
                        ],
                        "ocr_token_repairs": [
                            {"raw": "0-0", "corrected": "O-O", "confidence": 0.99, "notes": "castle"}
                        ],
                        "confidence": 0.88,
                        "requires_human_review": True,
                        "notes": "unit proposal",
                    }
                ),
                "usage": {"input_tokens": 20, "output_tokens": 12},
            }

        provider = OpenAIChessRepairProposer(
            model="unit-repair-model",
            mode="validated_export",
            api_key="sk-test",
            base_url="https://example.test/v1",
            reasoning_effort="low",
            transport=fake_transport,
        )

        proposal = provider.propose_chess_repair(
            {
                "record_id": "unit",
                "raw_ocr_text": "Diagram 1-1\n1. Kd3",
                "local_fen": "",
                "image_data": b"\x89PNG\r\n\x1a\nunit",
                "image_mime_type": "image/png",
            }
        )

        payload = captured["payload"]
        assert isinstance(payload, dict)
        content = payload["input"][0]["content"]
        self.assertEqual(captured["url"], "https://example.test/v1/responses")
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(payload["reasoning"]["effort"], "low")
        self.assertEqual(proposal["status"], "reviewed")
        self.assertEqual(proposal["mode"], "validated_export")
        self.assertFalse(proposal["mutates_exportable_pgn"])
        self.assertEqual(proposal["fen_candidates"][0]["fen"], "8/8/8/3k4/8/8/4K3/8 w - - 0 1")
        self.assertEqual(proposal["solution_line_candidates"][0]["movetext"], "1. Kd3")
        self.assertEqual(proposal["ocr_token_repairs"][0]["corrected"], "O-O")

        status = openai_chess_repair_proposer_status(
            env={
                "KINDLEMASTER_OPENAI_CHESS_REPAIR": "1",
                "OPENAI_API_KEY": "sk-test",
                "KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE": "validated_export",
            }
        )
        self.assertTrue(status["enabled"])
        self.assertEqual(status["mode"], "validated_export")
        self.assertTrue(status["requires_local_validation"])
        self.assertFalse(status["full_document_upload"])

    def test_semantic_cleanup_preserves_fen_on_chess_figure(self) -> None:
        fen = "8/8/8/3k4/8/8/4K3/8 w - - 0 1"
        soup = BeautifulSoup(
            f'<div class="chess-problem"><div class="chess-diagram-container" data-fen="{fen}" '
            'data-fen-confidence="0.960" data-fen-method="font-board">'
            '<img class="chess-diagram" src="images/board.png"/></div></div>',
            "xml",
        )

        normalized = _normalize_figure_html(soup.find("div", class_="chess-problem"))

        self.assertIn(f'data-fen="{fen}"', normalized)
        self.assertIn('data-fen-confidence="0.960"', normalized)
        self.assertIn('data-fen-method="font-board"', normalized)
        self.assertIn("Diagram szachowy, FEN:", normalized)
        self.assertIn(
            f'<p class="diagram-fen"><span class="diagram-fen-label">FEN:</span> '
            f'<code class="diagram-fen-code">{fen}</code></p>',
            normalized,
        )

    def test_semantic_cleanup_rebuilds_visible_fen_from_data_attribute(self) -> None:
        fen = "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 w - - 0 1"
        soup = BeautifulSoup(
            f'<div class="chess-problem"><div class="chess-diagram-container" data-fen="{fen}" '
            'data-fen-confidence="0.845" data-fen-method="image-template-board">'
            '<img class="chess-diagram" src="images/board.png"/></div>'
            '<p class="diagram-fen"><span class="diagram-fen-label">FEN:</span> '
            '<code class="diagram-fen-code">6k1/p4p1p/3p1p2/2p1r3/2 PnrqN1/P6P/1P1Q1PP1/3R1RK1 w - - 0 1</code></p>'
            "</div>",
            "xml",
        )

        normalized = _normalize_figure_html(soup.find("div", class_="chess-problem"))

        self.assertIn(f'<code class="diagram-fen-code">{fen}</code>', normalized)
        self.assertNotIn("2 PnrqN1", normalized)

    def test_semantic_cleanup_preserves_visible_fen_review_note(self) -> None:
        soup = BeautifulSoup(
            '<div class="chess-problem">'
            '<p class="diagram-caption">Strona 12, diagram 1</p>'
            '<div class="chess-diagram-container"><img class="chess-diagram" src="images/board.png"/></div>'
            '<p class="diagram-fen diagram-review" data-fen-status="requires-review">'
            "FEN: wymaga review - brak deterministycznej pewnosci figur."
            "</p>"
            "</div>",
            "xml",
        )

        normalized = _normalize_figure_html(soup.find("div", class_="chess-problem"))

        self.assertIn('class="diagram-fen diagram-review"', normalized)
        self.assertIn('data-fen-status="requires-review"', normalized)
        self.assertIn("FEN: wymaga review - brak deterministycznej pewnosci figur.", normalized)

    def test_scanned_chess_ocr_default_covers_all_detected_candidate_pages(self) -> None:
        self.assertEqual(ConversionConfig().scanned_chess_ocr_max_pages, 0)

    def test_scanned_chess_default_template_profile_points_to_reference_templates(self) -> None:
        config = ConversionConfig()

        self.assertEqual(config.chess_fen_template_profile, "fundamenty_merida_like")
        self.assertEqual(config.chess_fen_min_confidence, 0.835)
        self.assertFalse(config.chess_fen_emit_review_notes)
        self.assertTrue(config.chess_fen_apply_side_marker)
        self.assertFalse(config.chess_fen_review_provider_enabled)

    def test_scanned_chess_candidate_review_payload_does_not_invent_fen(self) -> None:
        payload = _scan_chess_candidate_review_payload(
            {
                "confidence": 0.74,
                "bbox": [1, 2, 81, 82],
                "method": "image-page-board-candidate",
                "warnings": ["piece_templates_unavailable"],
            }
        )

        self.assertEqual(payload["fen"], "")
        self.assertTrue(payload["requires_review"])
        self.assertEqual(payload["confidence"], 0.74)
        self.assertIn("image_board_requires_review", payload["warnings"])

    def test_scan_chess_side_marker_updates_fen_side_to_move(self) -> None:
        payload = {
            "fen": "8/8/8/8/8/8/7k/7K w - - 0 1",
            "side_to_move": "w",
            "warnings": ["side_to_move_inferred"],
        }

        updated = _apply_scan_chess_side_to_move_marker(payload, "b")

        self.assertEqual(updated["fen"], "8/8/8/8/8/8/7k/7K b - - 0 1")
        self.assertEqual(updated["side_to_move"], "b")
        self.assertNotIn("side_to_move_inferred", updated["warnings"])
        self.assertIn("side_to_move_marker_detected", updated["warnings"])

    def test_scan_chess_side_marker_infers_outline_white_and_filled_black(self) -> None:
        candidate_bbox = (100.0, 120.0, 900.0, 1120.0)
        board_bbox = (100.0, 260.0, 900.0, 1060.0)
        white_marker_page = Image.new("RGB", (1080, 1200), "white")
        black_marker_page = Image.new("RGB", (1080, 1200), "white")
        for image in (white_marker_page, black_marker_page):
            draw = ImageDraw.Draw(image)
            draw.rectangle(board_bbox, outline="black", width=3)
        ImageDraw.Draw(white_marker_page).line(
            [(740, 145), (800, 145), (770, 198), (740, 145)],
            fill="black",
            width=5,
        )
        ImageDraw.Draw(black_marker_page).polygon(
            [(740, 145), (800, 145), (770, 198)],
            fill="black",
        )

        self.assertEqual(_infer_scan_chess_side_to_move(white_marker_page, candidate_bbox), "w")
        self.assertEqual(_infer_scan_chess_side_to_move(black_marker_page, candidate_bbox), "b")

    def test_scan_chess_side_marker_ignores_coordinate_letters_above_board(self) -> None:
        board_bbox = (100.0, 170.0, 300.0, 370.0)
        page = Image.new("RGB", (420, 420), "white")
        draw = ImageDraw.Draw(page)
        draw.text((245, 122), "g", fill="black")
        draw.text((290, 122), "h", fill="black")
        draw.rectangle(board_bbox, outline="black", width=3)

        self.assertEqual(_infer_scan_chess_side_to_move(page, board_bbox), "")

    def test_scan_chess_side_marker_ignores_top_right_board_piece(self) -> None:
        board_bbox = (100.0, 170.0, 300.0, 370.0)
        page = Image.new("RGB", (420, 420), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle(board_bbox, outline="black", width=3)
        draw.ellipse((250, 182, 292, 228), outline="black", width=6)
        draw.rectangle((260, 222, 282, 250), fill="black")

        self.assertEqual(_infer_scan_chess_side_to_move(page, board_bbox), "")

    def test_scanned_chess_ocr_normalizes_common_figurine_artifacts(self) -> None:
        text = _normalize_scan_chess_ocr_text("1...De2t! 2.Axd2 Wxh2t 3.&g4 @h1 4.0c3 5.@2h1")

        self.assertIn("1...Qe2+!", text)
        self.assertIn("2.Nxd2", text)
        self.assertIn("Rxh2+", text)
        self.assertIn("Bg4", text)
        self.assertIn("Kh1", text)
        self.assertIn("Nc3", text)

    def test_scanned_chess_ocr_html_marks_notation_lines(self) -> None:
        parts = _scan_chess_ocr_html_parts(
            {
                "text": "Legal's mate\n1.e4 e5 2.Nf3 Nc6 3.Bc4 d6\nDiagram 2-2",
                "confidence": 0.82,
            },
            page_num=18,
        )
        html = "".join(parts)

        self.assertIn("OCR strony 19", html)
        self.assertIn("chess-notation-text", html)
        self.assertIn("data-ocr-confidence=\"0.820\"", html)

    def test_scanned_chess_front_matter_metadata_infers_title_author_and_publisher(self) -> None:
        metadata = _infer_scan_chess_front_matter_metadata(
            [
                {"page_num": 0, "confidence": 0.92, "text": "ARTUR YUSUPOV\n\nTHE FUNDAMENTALS"},
                {
                    "page_num": 2,
                    "confidence": 0.94,
                    "text": "Build Up Your Chess\nwith Artur Yusupov\n\nThe Fundamentals\n\nArtur Yusupov\nQuality Chess",
                },
            ]
        )

        self.assertEqual(metadata["author"], "Artur Yusupov")
        self.assertEqual(metadata["publisher"], "Quality Chess")
        self.assertEqual(metadata["title"], "Build Up Your Chess with Artur Yusupov: The Fundamentals")


def _board_png(size: tuple[int, int]) -> bytes:
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    cell = min(size) // 8
    for row in range(8):
        for col in range(8):
            fill = 245 if (row + col) % 2 == 0 else 30
            draw.rectangle(
                (col * cell, row * cell, (col + 1) * cell - 1, (row + 1) * cell - 1),
                fill=fill,
            )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _image_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _labeled_board_png_and_templates(board: list[list[str]]) -> tuple[bytes, dict[str, list[Image.Image]]]:
    cell = 40
    image = Image.new("L", (cell * 8, cell * 8), 255)
    draw = ImageDraw.Draw(image)
    templates: dict[str, list[Image.Image]] = {}
    for row in range(8):
        for col in range(8):
            fill = 238 if (row + col) % 2 == 0 else 118
            box = (col * cell, row * cell, (col + 1) * cell - 1, (row + 1) * cell - 1)
            draw.rectangle(box, fill=fill)
            piece = board[row][col]
            if piece:
                draw.text((col * cell + 14, row * cell + 13), piece, fill=0)

    for row in range(8):
        for col in range(8):
            piece = board[row][col]
            label = piece or ""
            box = (col * cell, row * cell, (col + 1) * cell, (row + 1) * cell)
            templates.setdefault(label, []).append(image.crop(box))

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), templates


if __name__ == "__main__":
    unittest.main()


class ScanChessPreprocessingTests(unittest.TestCase):
    def test_notation_diagram_number_prefers_nearest_caption(self) -> None:
        from types import SimpleNamespace

        from pymupdf_chess_extractor import _nearest_chess_notation_diagram_caption_match, _nearest_chess_notation_diagram_number

        line_items = [
            SimpleNamespace(text="Diagram 1-1", y=40.0, x0=20.0, x1=180.0),
            SimpleNamespace(text="Diagram 1-2 A. Yusupov", y=118.0, x0=210.0, x1=430.0),
            SimpleNamespace(text="Diagram 1-3", y=420.0, x0=20.0, x1=180.0),
        ]

        match = _nearest_chess_notation_diagram_caption_match(line_items, (220.0, 140.0, 390.0, 310.0))
        self.assertIsNotNone(match)
        self.assertEqual(match.diagram_number, "1-2")
        self.assertGreaterEqual(match.score, 70)
        self.assertEqual(
            _nearest_chess_notation_diagram_number(line_items, (220.0, 140.0, 390.0, 310.0)),
            "1-2",
        )

    def test_notation_diagram_caption_recovers_split_ocr_number(self) -> None:
        from types import SimpleNamespace

        from pymupdf_chess_extractor import _nearest_chess_notation_diagram_caption_match

        line_items = [
            SimpleNamespace(text="Diagram", y=80.0, x0=210.0, x1=270.0, font_size=12.0),
            SimpleNamespace(text="1 1-2 A. Yusupov", y=80.0, x0=274.0, x1=430.0, font_size=12.0),
            SimpleNamespace(text="Diagram 1-3", y=82.0, x0=20.0, x1=160.0, font_size=12.0),
        ]

        match = _nearest_chess_notation_diagram_caption_match(line_items, (220.0, 110.0, 390.0, 280.0))

        self.assertIsNotNone(match)
        self.assertEqual(match.diagram_number, "1-2")
        self.assertGreaterEqual(match.score, 70)

    def test_notation_diagram_caption_penalizes_other_column(self) -> None:
        from types import SimpleNamespace

        from pymupdf_chess_extractor import _nearest_chess_notation_diagram_caption_match

        line_items = [
            SimpleNamespace(text="Diagram 2-1", y=92.0, x0=20.0, x1=170.0, font_size=12.0),
            SimpleNamespace(text="Diagram 2-2", y=96.0, x0=225.0, x1=390.0, font_size=12.0),
        ]

        match = _nearest_chess_notation_diagram_caption_match(line_items, (230.0, 125.0, 390.0, 285.0))

        self.assertIsNotNone(match)
        self.assertEqual(match.diagram_number, "2-2")

    def test_notation_diagram_caption_recovers_noisy_diagram_1_13(self) -> None:
        from types import SimpleNamespace

        from pymupdf_chess_extractor import _nearest_chess_notation_diagram_caption_match

        line_items = [
            SimpleNamespace(
                text="Diagram 1-13 \ufffdiiiiI Dreszer Open, Gdynia 1989",
                y=95.0,
                x0=30.0,
                x1=390.0,
                font_size=12.0,
            )
        ]

        match = _nearest_chess_notation_diagram_caption_match(line_items, (42.0, 128.0, 202.0, 288.0))

        self.assertIsNotNone(match)
        self.assertEqual(match.diagram_number, "1-13")
        self.assertGreaterEqual(match.score, 70)

    def test_notation_chess_display_crop_trims_caption_margin(self) -> None:
        from PIL import Image, ImageDraw

        from pymupdf_chess_extractor import _notation_chess_display_crop

        image = Image.new("RGB", (260, 310), "white")
        draw = ImageDraw.Draw(image)
        cell = 20
        for row in range(8):
            for col in range(8):
                fill = 235 if (row + col) % 2 == 0 else 130
                draw.rectangle(
                    (40 + col * cell, 90 + row * cell, 40 + (col + 1) * cell, 90 + (row + 1) * cell),
                    fill=fill,
                    outline="black",
                )

        crop, quality = _notation_chess_display_crop(image)

        self.assertLess(crop.height, image.height)
        self.assertTrue(quality["trimmed"])
        self.assertGreaterEqual(quality["contrast"], 20)

    def test_caption_guided_scan_adds_local_board_candidate(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        from PIL import Image

        from chess_position_recognizer import ChessFenResult
        from pymupdf_chess_extractor import _augment_notation_board_candidates_from_captions

        page_image = Image.new("RGB", (600, 800), "white")
        local_candidate = ChessFenResult(
            confidence=0.77,
            bbox=(20.0, 30.0, 180.0, 190.0),
            method="local-board",
            requires_review=True,
            board_detected=True,
        )

        class FakeDoc:
            def __getitem__(self, index):
                return SimpleNamespace(rect=SimpleNamespace(width=400.0, height=533.0))

        with mock.patch(
            "pymupdf_chess_extractor.detect_board_candidates_in_page_image",
            return_value=[local_candidate],
        ):
            candidates = _augment_notation_board_candidates_from_captions(
                FakeDoc(),
                0,
                page_image,
                [],
                [
                    SimpleNamespace(
                        text="Diagram 1-13 \ufffdiiiiI Dreszer Open",
                        y=60.0,
                        x0=30.0,
                        x1=250.0,
                        font_size=12.0,
                    )
                ],
                max_candidates=1,
            )

        self.assertEqual(len(candidates), 1)
        self.assertIn("caption_guided", candidates[0].method)
        self.assertTrue(candidates[0].board_detected)

    def test_scan_chess_preprocessed_variants_select_highest_confidence(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        from PIL import Image, ImageDraw

        from converter import ConversionConfig
        from pymupdf_chess_extractor import _recognize_scan_chess_preprocessed_variants

        crop = Image.new("L", (160, 160), 240)
        draw = ImageDraw.Draw(crop)
        for index in range(9):
            pos = index * 20
            draw.line((pos, 0, pos, 160), fill=20, width=2)
            draw.line((0, pos, 160, pos), fill=20, width=2)

        confidences = iter([0.31, 0.88, 0.62, 0.44])

        def fake_recognize(*args, **kwargs):
            confidence = next(confidences)
            return SimpleNamespace(confidence=confidence, board_detected=True)

        with mock.patch("pymupdf_chess_extractor.recognize_chess_position_from_image", side_effect=fake_recognize):
            result, selected_variant, metadata = _recognize_scan_chess_preprocessed_variants(
                crop,
                config=ConversionConfig(),
                piece_templates={"K": [Image.new("L", (12, 12), 0)]},
                bbox=(0.0, 0.0, 160.0, 160.0),
            )

        self.assertEqual(selected_variant, "autocontrast")
        self.assertAlmostEqual(result.confidence, 0.88)
        self.assertEqual(metadata["selected_preprocess_variant"], "autocontrast")
        self.assertEqual(metadata["display_variant_used"], "reader_enhanced")

    def test_scan_chess_preprocessed_variants_keep_original_when_best(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        from PIL import Image

        from converter import ConversionConfig
        from pymupdf_chess_extractor import _recognize_scan_chess_preprocessed_variants

        crop = Image.new("L", (160, 160), 220)
        confidences = iter([0.91, 0.72, 0.70, 0.45])

        def fake_recognize(*args, **kwargs):
            return SimpleNamespace(confidence=next(confidences), board_detected=True)

        with mock.patch("pymupdf_chess_extractor.recognize_chess_position_from_image", side_effect=fake_recognize):
            result, selected_variant, metadata = _recognize_scan_chess_preprocessed_variants(
                crop,
                config=ConversionConfig(),
                piece_templates={"K": [Image.new("L", (12, 12), 0)]},
            )

        self.assertEqual(selected_variant, "original")
        self.assertAlmostEqual(result.confidence, 0.91)
        self.assertEqual(metadata["selected_preprocess_variant"], "original")

    def test_notation_diagram_record_uses_full_fen_recognizer(self) -> None:
        from unittest import mock

        from converter import ConversionConfig
        from pymupdf_chess_extractor import _chess_notation_diagram_records_from_page

        page = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(page)
        cell = 20
        for row in range(8):
            for col in range(8):
                fill = 235 if (row + col) % 2 == 0 else 130
                draw.rectangle(
                    (40 + col * cell, 40 + row * cell, 40 + (col + 1) * cell, 40 + (row + 1) * cell),
                    fill=fill,
                    outline="black",
                )
        page_data = _image_bytes(page)
        candidate = ChessFenResult(
            confidence=0.73,
            bbox=(40.0, 40.0, 200.0, 200.0),
            method="image-page-board-candidate",
            requires_review=True,
            board_detected=True,
        )
        recognition = ChessFenResult(
            fen="8/8/8/8/8/8/7k/7K w - - 0 1",
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.91,
            method="image-template-board",
            warnings=["side_to_move_inferred"],
            requires_review=False,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._page_image_data_for_scan_chess",
            return_value=page_data,
        ), mock.patch(
            "pymupdf_chess_extractor.detect_board_candidates_in_page_image",
            return_value=[candidate],
        ), mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox",
            return_value=recognition,
        ), mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_preprocessed_variants",
            return_value=(None, "original", {}),
        ):
            records = _chess_notation_diagram_records_from_page(
                object(),
                0,
                config=ConversionConfig(chess_fen_min_confidence=0.835),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                line_items=[
                    type(
                        "Line",
                        (),
                        {"text": "Diagram 1-2 A. Yusupov", "y": 20.0, "x0": 40.0, "x1": 200.0, "font_size": 12.0},
                    )()
                ],
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["diagram_number"], "1-2")
        self.assertGreaterEqual(records[0]["caption_match_score"], 70)
        self.assertIn("Diagram 1-2", records[0]["caption_text"])
        self.assertEqual(records[0]["fen"], recognition.fen)
        self.assertEqual(records[0]["placement"], recognition.placement)
        self.assertFalse(records[0]["requires_review"])
        self.assertEqual(records[0]["selected_preprocess_variant"], "full_page_bbox_recognition")
        self.assertEqual(records[0]["display_variant_used"], "reader_enhanced")
        self.assertGreater(records[0]["fen_confidence"], 0.8)

    def test_notation_diagram_record_keeps_low_confidence_fen_in_review(self) -> None:
        from unittest import mock

        from converter import ConversionConfig
        from pymupdf_chess_extractor import _chess_notation_diagram_records_from_page

        page = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(page)
        cell = 20
        for row in range(8):
            for col in range(8):
                fill = 235 if (row + col) % 2 == 0 else 130
                draw.rectangle(
                    (40 + col * cell, 40 + row * cell, 40 + (col + 1) * cell, 40 + (row + 1) * cell),
                    fill=fill,
                    outline="black",
                )
        page_data = _image_bytes(page)
        candidate = ChessFenResult(
            confidence=0.68,
            bbox=(40.0, 40.0, 200.0, 200.0),
            method="image-page-board-candidate",
            requires_review=True,
            board_detected=True,
        )
        recognition = ChessFenResult(
            placement="8/8/8/8/8/8/7k/7K",
            confidence=0.52,
            method="image-template-board",
            warnings=["piece_template_confidence_below_threshold"],
            requires_review=True,
            board_detected=True,
        )

        with mock.patch(
            "pymupdf_chess_extractor._page_image_data_for_scan_chess",
            return_value=page_data,
        ), mock.patch(
            "pymupdf_chess_extractor.detect_board_candidates_in_page_image",
            return_value=[candidate],
        ), mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox",
            return_value=recognition,
        ), mock.patch(
            "pymupdf_chess_extractor._recognize_scan_chess_preprocessed_variants",
            return_value=(None, "original", {}),
        ):
            records = _chess_notation_diagram_records_from_page(
                object(),
                0,
                config=ConversionConfig(chess_fen_min_confidence=0.835),
                piece_templates={"K": [Image.new("L", (10, 10), 0)]},
                line_items=[],
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["fen"], "")
        self.assertTrue(records[0]["requires_review"])
        self.assertIn("piece_template_confidence_below_threshold", records[0]["warnings"])
        self.assertEqual(records[0]["display_variant_used"], "reader_enhanced")


if __name__ == "__main__":
    unittest.main()
