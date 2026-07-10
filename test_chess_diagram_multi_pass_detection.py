from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image

from chess_diagram_fingerprint import source_document_sha256
from chess_position_recognizer import ChessFenResult
from chess_study_export import (
    ChessStudyConfig,
    _predict_fen_for_source,
    build_study_positions,
    detect_study_diagrams,
)
from scripts.chess_diagram_detection import detect_chess_diagrams


VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


def _make_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=180, height=120)
    page.insert_text((10, 18), "Synthetic diagram page", fontsize=8)
    document.save(path)
    document.close()


def _candidate(image_size: tuple[int, int], bbox: tuple[float, float, float, float], confidence: float) -> ChessFenResult:
    width, height = image_size
    x0, y0, x1, y1 = bbox
    return ChessFenResult(
        confidence=confidence,
        bbox=(x0 * width, y0 * height, x1 * width, y1 * height),
        method="synthetic-grid",
        requires_review=True,
        board_detected=True,
    )


class ChessDiagramMultiPassDetectionTests(unittest.TestCase):
    def test_multi_pass_nms_adaptive_recovery_and_expected_recall(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            _make_pdf(pdf_path)
            source_sha = source_document_sha256(pdf_path)
            expected_path = root / "expected.json"
            expected_path.write_text(
                json.dumps(
                    {
                        "source_document_sha256": source_sha,
                        "diagrams": [
                            {"page": 1, "normalized_bbox_xyxy": [0.10, 0.10, 0.42, 0.58]},
                            {"page": 1, "normalized_bbox_xyxy": [0.55, 0.10, 0.90, 0.55]},
                            {"page": 1, "normalized_bbox_xyxy": [0.10, 0.60, 0.45, 0.95]},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            calls: list[dict[str, object]] = []

            def fake_detect(page_png: bytes, **kwargs: object) -> list[ChessFenResult]:
                with Image.open(io.BytesIO(page_png)) as image:
                    size = image.size
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    return [_candidate(size, (0.10, 0.10, 0.42, 0.58), 0.92)]
                if len(calls) == 2:
                    return [
                        _candidate(size, (0.101, 0.101, 0.421, 0.581), 0.91),
                        _candidate(size, (0.55, 0.10, 0.90, 0.55), 0.88),
                    ]
                return [
                    _candidate(size, (0.10, 0.10, 0.42, 0.58), 0.70),
                    _candidate(size, (0.55, 0.10, 0.90, 0.55), 0.68),
                    _candidate(size, (0.10, 0.60, 0.45, 0.95), 0.45),
                ]

            untrusted_fen = ChessFenResult(
                fen=VALID_FEN,
                full_fen=VALID_FEN,
                placement=VALID_FEN.split()[0],
                confidence=0.99,
                side_to_move="w",
                side_to_move_status="unknown",
                side_to_move_evidence="none",
                requires_review=False,
                board_detected=True,
            )
            with (
                patch(
                    "scripts.chess_diagram_detection.detect_board_candidates_in_page_image",
                    side_effect=fake_detect,
                ),
                patch("scripts.chess_diagram_detection._recognize_crop_fen", return_value=untrusted_fen),
            ):
                manifest = detect_chess_diagrams(
                    pdf_path,
                    output_dir=root / "out",
                    dpi=96,
                    detection_dpis=[96, 144],
                    max_candidates_per_page=2,
                    min_grid_confidence=0.70,
                    low_confidence_min_grid_confidence=0.30,
                    low_confidence_max_candidates_per_page=4,
                    expected_diagram_manifest=expected_path,
                    template_dir="",
                )

        self.assertEqual(len(calls), 3)
        self.assertFalse(bool(calls[0]["enable_sliding_probe"]))
        self.assertFalse(bool(calls[1]["enable_sliding_probe"]))
        self.assertTrue(bool(calls[2]["enable_sliding_probe"]))
        self.assertEqual(manifest["strict_diagram_count"], 2)
        self.assertEqual(manifest["recovered_diagram_count"], 1)
        self.assertEqual(manifest["diagram_count"], 3)
        self.assertEqual(manifest["expected_diagram_recall"]["status"], "passed")
        self.assertEqual(manifest["expected_diagram_recall"]["expected_diagram_recall"], 1.0)
        self.assertEqual(len({row["diagram_fingerprint"] for row in manifest["diagrams"]}), 3)
        self.assertGreaterEqual(len(manifest["diagrams"][0]["detection_passes"]), 2)
        self.assertTrue(all(row["side_to_move"] == "unknown" for row in manifest["diagrams"]))
        self.assertTrue(all(not row.get("fen") for row in manifest["diagrams"]))
        self.assertTrue(all(not row.get("fen_candidate") for row in manifest["diagrams"]))
        self.assertEqual(manifest["diagrams"][0]["reason"], "side_to_move_not_explicit")
        recovered = manifest["low_confidence_review_candidates"][0]
        self.assertEqual(recovered["candidate_tier"], "recovered")
        self.assertFalse(recovered["full_fen_allowed"])

    def test_recovered_candidates_are_canonical_and_enter_marker_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            strict_crop = root / "strict.webp"
            recovered_crop = root / "recovered.webp"
            Image.new("RGB", (96, 96), "white").save(strict_crop, format="WEBP")
            Image.new("RGB", (96, 96), "gray").save(recovered_crop, format="WEBP")
            strict = {
                "diagram_id": "p001_d01",
                "legacy_diagram_id": "p001_d01",
                "diagram_fingerprint": "dfp_strict",
                "source_document_sha256": "d" * 64,
                "normalized_bbox_xyxy": [0.1, 0.1, 0.4, 0.4],
                "candidate_tier": "strict",
                "page": 1,
                "bbox": [10, 10, 40, 40],
                "image_path": str(strict_crop),
                "confidence": 0.9,
                "status": "needs_review",
                "fen": "",
                "side_to_move": "unknown",
            }
            recovered = {
                "diagram_id": "p001_lc01",
                "legacy_diagram_id": "p001_lc01",
                "diagram_fingerprint": "dfp_recovered",
                "source_document_sha256": "d" * 64,
                "normalized_bbox_xyxy": [0.5, 0.1, 0.8, 0.4],
                "candidate_tier": "recovered",
                "page": 1,
                "bbox": [50, 10, 40, 40],
                "image_path": str(recovered_crop),
                "confidence": 0.4,
                "status": "needs_review",
                "fen": "",
                "side_to_move": "unknown",
                "review_only": True,
            }
            fake_manifest = {
                "diagram_count": 2,
                "strict_diagram_count": 1,
                "recovered_diagram_count": 1,
                "diagrams": [strict, recovered],
                "low_confidence_review_candidates": [recovered],
            }
            routed: list[str] = []

            def fake_marker_pipeline(
                _pdf_path: str | Path,
                diagrams: list[dict[str, object]],
                _out_dir: str | Path,
                **_kwargs: object,
            ) -> dict[str, object]:
                routed.extend(str(row.get("diagram_fingerprint") or "") for row in diagrams)
                return {"diagram_count": len(diagrams), "status": "synthetic"}

            config = ChessStudyConfig(pdf=root / "missing.pdf", html=None, out=root / "out")
            with (
                patch("chess_study_export.detect_chess_diagrams", return_value=fake_manifest),
                patch(
                    "chess_study_export._attach_pdf_side_marker_evidence_to_study_diagrams",
                    side_effect=fake_marker_pipeline,
                ),
            ):
                diagrams = detect_study_diagrams(config)
            positions = build_study_positions(
                diagrams,
                {"pages": [{"page": 1, "chapter_no": 1, "labels": ["Ex. 1-1"]}]},
                root / "out",
            )

        self.assertEqual(routed, ["dfp_strict", "dfp_recovered"])
        self.assertEqual(len(diagrams["diagrams"]), 2)
        self.assertEqual(len(diagrams["low_confidence_review_candidates"]), 1)
        self.assertEqual(len(positions["positions"]), 2)
        recovered_position = next(
            row for row in positions["positions"] if row["diagram_fingerprint"] == "dfp_recovered"
        )
        self.assertEqual(recovered_position["candidate_tier"], "recovered")
        self.assertEqual(recovered_position["fen"], "")

    def test_model_prediction_never_defaults_missing_side_to_white(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            crop_path = Path(temp_dir) / "board.png"
            board = Image.new("RGB", (80, 80), "white")
            board.save(crop_path)
            square = Image.new("L", (10, 10), "white")
            with (
                patch("chess_study_export._normalize_board_image", return_value=board),
                patch("chess_study_export._split_board_into_squares", return_value=[square] * 64),
                patch(
                    "chess_study_export._predict_square_class",
                    return_value={"class": "", "label": "empty", "confidence": 1.0, "entropy": 0.0},
                ),
            ):
                row = _predict_fen_for_source(
                    {"diagram_id": "unknown-side", "crop_path": str(crop_path), "caption": "Exercise 1"},
                    Path(temp_dir),
                    {},
                )

        self.assertEqual(row["placement"], "8/8/8/8/8/8/8/8")
        self.assertEqual(row["fen_candidate"], "")
        self.assertFalse(row["deterministic_validation"]["valid"])
        self.assertIn("side_to_move_unknown", row["deterministic_validation"]["warnings"])


if __name__ == "__main__":
    unittest.main()
