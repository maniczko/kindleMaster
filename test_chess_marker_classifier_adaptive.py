from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from chess_diagram_fingerprint import build_diagram_fingerprint
from chess_marker_classifier_adaptive import (
    GRAMMAR_PROFILES,
    _bbox_iou,
    _correlation,
    fit_reliability_calibration,
    reliability_metrics,
)
from chess_marker_crop_classifier import evaluate_marker_crop_corpus
from pymupdf_chess_extractor import classify_scan_chess_side_marker_crop


SOURCE_SHA = "3" * 64


class AdaptiveMarkerDefensiveHelperTests(unittest.TestCase):
    def test_correlation_rejects_mismatched_lengths(self) -> None:
        self.assertEqual(_correlation([1.0, 2.0], [1.0, 2.0, 3.0]), 0.0)

    def test_bbox_iou_rejects_short_sequences(self) -> None:
        self.assertEqual(_bbox_iou([0, 0, 1], [0, 0, 1, 1]), 0.0)
        self.assertEqual(_bbox_iou([0, 0, 1, 1], [0, 0, 1]), 0.0)


def _outline(size: int = 96, *, ink: int = 20, background: int = 245) -> Image.Image:
    image = Image.new("L", (size, size), background)
    draw = ImageDraw.Draw(image)
    margin = round(size * 0.16)
    draw.line(
        [
            (size // 2, margin),
            (margin, size - margin),
            (size - margin, size - margin),
            (size // 2, margin),
        ],
        fill=ink,
        width=max(2, round(size * 0.045)),
        joint="curve",
    )
    return image


def _filled_inverted(size: int = 96, *, ink: int = 20, background: int = 245) -> Image.Image:
    image = Image.new("L", (size, size), background)
    margin = round(size * 0.16)
    ImageDraw.Draw(image).polygon(
        [(margin, margin), (size - margin, margin), (size // 2, size - margin)],
        fill=ink,
    )
    return image


def _fingerprint(image: Image.Image, page: int) -> dict[str, object]:
    return build_diagram_fingerprint(
        source_sha256=SOURCE_SHA,
        page=page,
        normalized_bbox_xyxy=[0.1, 0.1, 0.5, 0.5],
        board_crop=image,
    )


class ChessMarkerClassifierAdaptiveTests(unittest.TestCase):
    def test_dpi_contrast_antialias_and_blur_perturbations_keep_clear_markers(self) -> None:
        sources = [("w", _outline()), ("b", _filled_inverted())]
        for expected_side, source in sources:
            for size in (40, 64, 96, 144):
                perturbed = source.resize((size, size), Image.Resampling.LANCZOS)
                perturbed = ImageEnhance.Contrast(perturbed).enhance(0.72)
                if size in {64, 144}:
                    perturbed = perturbed.filter(ImageFilter.GaussianBlur(0.35))
                with self.subTest(side=expected_side, size=size):
                    result = classify_scan_chess_side_marker_crop(perturbed)
                    self.assertEqual(result["status"], "trusted_marker", result)
                    self.assertEqual(result["side"], expected_side, result)
                    self.assertIn("otsu", " ".join(result["segmentation_methods"]))
                    self.assertGreaterEqual(result["component"]["triangularity"], 0.52)

    def test_orientation_fill_disagreement_is_review_only(self) -> None:
        upright_filled = Image.new("L", (80, 80), "white")
        ImageDraw.Draw(upright_filled).polygon([(40, 8), (8, 70), (72, 70)], fill="black")
        inverted_outline = Image.new("L", (80, 80), "white")
        ImageDraw.Draw(inverted_outline).line(
            [(8, 8), (72, 8), (40, 70), (8, 8)],
            fill="black",
            width=3,
            joint="curve",
        )

        for crop in (upright_filled, inverted_outline):
            result = classify_scan_chess_side_marker_crop(crop)
            self.assertNotEqual(result["status"], "trusted_marker", result)
            self.assertEqual(result["side_to_move"], "unknown")
            self.assertEqual(result["reason"], "orientation_fill_disagreement")

    def test_morphology_groups_one_broken_outline_triangle(self) -> None:
        crop = Image.new("L", (84, 84), "white")
        draw = ImageDraw.Draw(crop)
        draw.line([(42, 10), (12, 70)], fill="black", width=4)
        draw.line([(15, 70), (69, 70)], fill="black", width=4)
        draw.line([(72, 70), (42, 13)], fill="black", width=4)

        result = classify_scan_chess_side_marker_crop(crop)

        self.assertEqual(result["status"], "trusted_marker", result)
        self.assertEqual(result["side"], "w")
        self.assertTrue(
            any("close" in method for method in result["segmentation_methods"]),
            result,
        )

    def test_text_border_arrow_caption_and_multiple_shapes_never_become_trusted(self) -> None:
        samples: list[Image.Image] = []
        text = Image.new("L", (80, 80), "white")
        ImageDraw.Draw(text).text((28, 20), "8a", fill="black")
        samples.append(text)
        border = Image.new("L", (80, 80), "white")
        ImageDraw.Draw(border).rectangle((2, 2, 76, 76), outline="black", width=4)
        samples.append(border)
        arrow = Image.new("L", (80, 80), "white")
        draw = ImageDraw.Draw(arrow)
        draw.line((10, 40, 68, 40), fill="black", width=5)
        draw.polygon([(68, 25), (78, 40), (68, 55)], fill="black")
        samples.append(arrow)
        caption = Image.new("L", (120, 60), "white")
        ImageDraw.Draw(caption).text((8, 18), "Diagram 14...", fill="black")
        samples.append(caption)
        multiple = Image.new("L", (120, 80), "white")
        draw = ImageDraw.Draw(multiple)
        draw.line([(30, 8), (8, 66), (52, 66), (30, 8)], fill="black", width=3)
        draw.polygon([(68, 8), (110, 8), (89, 66)], fill="black")
        samples.append(multiple)

        for index, crop in enumerate(samples):
            with self.subTest(index=index):
                result = classify_scan_chess_side_marker_crop(crop)
                self.assertNotEqual(result["status"], "trusted_marker", result)

    def test_calibration_uses_only_calibration_rows_and_reports_reliability(self) -> None:
        rows = [
            {"split": "train", "raw_confidence": 0.99, "correct": False},
            {"split": "calibration", "raw_confidence": 0.70, "correct": True},
            {"split": "calibration", "raw_confidence": 0.90, "correct": True},
            {"split": "calibration", "raw_confidence": 0.95, "correct": False},
            {"split": "holdout", "raw_confidence": 0.10, "correct": False},
        ]

        calibration = fit_reliability_calibration(rows)
        reliability = reliability_metrics(
            [
                {"confidence": 0.8, "correct": True},
                {"confidence": 0.9, "correct": False},
            ]
        )

        self.assertEqual(calibration["source_split"], "calibration")
        self.assertEqual(calibration["sample_count"], 3)
        self.assertEqual(calibration["rejected_non_calibration_count"], 2)
        self.assertFalse(calibration["holdout_used_for_tuning"])
        self.assertEqual(reliability["sample_count"], 2)
        self.assertGreater(reliability["expected_calibration_error"], 0.0)

    def test_runtime_grammar_matches_committed_source_profile(self) -> None:
        profile_path = (
            Path(__file__).parent
            / "reference_inputs"
            / "chess_marker_acceptance"
            / "profiles"
            / "yusupov-fundamentals.json"
        )
        committed = json.loads(profile_path.read_text(encoding="utf-8"))
        runtime = GRAMMAR_PROFILES["yusupov-fundamentals"]

        self.assertEqual(committed["marker_grammar"]["white"], runtime["white"])
        self.assertEqual(committed["marker_grammar"]["black"], runtime["black"])
        self.assertEqual(committed["classifier_policy"]["calibration_split"], "calibration")
        self.assertFalse(committed["classifier_policy"]["holdout_used_for_tuning"])

    def test_real_manifest_reports_holdout_separately_without_tuning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crops = root / "micro_crops"
            crops.mkdir()
            diagrams = []
            specs = [
                ("train", 1, "chapter-train", "w", _outline()),
                ("calibration", 2, "chapter-calibration", "b", _filled_inverted()),
                ("holdout", 3, "chapter-holdout", "w", _outline(88)),
            ]
            for split, page, chapter, side, image in specs:
                path = crops / f"{split}.png"
                image.save(path)
                diagrams.append(
                    {
                        **_fingerprint(image, page),
                        "chapter_id": chapter,
                        "split": split,
                        "allowed_for_tuning": split != "holdout",
                        "marker_status": "present",
                        "expected_side": side,
                        "marker_ownership": "assigned",
                        "crop_quality": "clear",
                        "source_of_truth": "dual_human",
                        "expected_fallback_source": "none",
                        "label_status": "verified",
                        "marker_crop_path": path.relative_to(root).as_posix(),
                    }
                )
            hard_negatives = []
            kinds = (
                "coordinates",
                "letters",
                "borders",
                "arrows",
                "captions",
                "neighboring_diagrams",
            )
            for index, kind in enumerate(kinds):
                split, page, chapter = specs[index // 2][:3]
                crop = Image.new("L", (72, 72), "white")
                ImageDraw.Draw(crop).text((10, 25), f"{index}a", fill="black")
                path = crops / f"negative-{index}.png"
                crop.save(path)
                hard_negatives.append(
                    {
                        "hard_negative_fingerprint": "hnf_" + sha256(kind.encode()).hexdigest()[:32],
                        "kind": kind,
                        "page": page,
                        "chapter_id": chapter,
                        "split": split,
                        "allowed_for_tuning": split != "holdout",
                        "normalized_bbox_xyxy": [0.6, 0.1, 0.8, 0.3],
                        "source_of_truth": "dual_human",
                        "label_status": "verified",
                        "expected_disposition": "reject",
                        "crop_path": path.relative_to(root).as_posix(),
                    }
                )
            manifest = {
                "schema": "kindlemaster.chess.marker_acceptance_manifest.v1",
                "source_profile": "yusupov-fundamentals",
                "source": {
                    "kind": "fixed_edition_pdf",
                    "sha256": SOURCE_SHA,
                    "copyright_content_committed": False,
                },
                "verification": {
                    "status": "verified",
                    "verified_by": "reviewer-a+reviewer-b",
                    "verified_at": "2026-07-10T00:00:00Z",
                },
                "diagrams": diagrams,
                "hard_negatives": hard_negatives,
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            report = evaluate_marker_crop_corpus(root, report_path=root / "report.json")

        self.assertEqual(report["summary"]["corpus_kind"], "real_fixed_edition")
        self.assertEqual(report["summary"]["decision"], "pass", report)
        self.assertEqual(report["calibration"]["source_split"], "calibration")
        self.assertFalse(report["policy"]["holdout_used_for_tuning"])
        self.assertEqual(report["real_fixed_edition_holdout"]["status"], "evaluated")
        self.assertEqual(report["real_fixed_edition_holdout"]["clear_marker_classification_accuracy"], 1.0)
        self.assertEqual(report["real_fixed_edition_holdout"]["false_trusted_marker_count"], 0)
        self.assertEqual(report["summary"]["required_clear_marker_accuracy"], 1.0)
        self.assertEqual(
            report["summary"]["required_clear_marker_accuracy_source"],
            "fixed_edition_acceptance_profile",
        )


if __name__ == "__main__":
    unittest.main()
