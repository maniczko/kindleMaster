from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app_runtime_services import ConversionRequest, build_conversion_config
from chess_fen_runtime import (
    DEFAULT_FEN_MODEL_PATH,
    apply_fen_square_runtime,
    fen_runtime_scope,
    load_marker_confidence_calibration,
)
from chess_fen_square_model import (
    export_portable_fen_square_model,
    load_portable_fen_square_model,
    predict_portable_fen_board,
)
from chess_position_recognizer import ChessFenResult, summarize_chess_fen_results
from converter import ConversionConfig
from pymupdf_chess_extractor import (
    _recognize_chess_position_with_runtime,
    classify_scan_chess_side_marker_crop,
)


PLACEMENT = "4k3/8/8/8/8/8/8/4K3"


def _board_bytes() -> bytes:
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    for rank in range(8):
        for file_index in range(8):
            color = "#f0d9b5" if (rank + file_index) % 2 == 0 else "#7b5238"
            draw.rectangle(
                (
                    file_index * 32,
                    rank * 32,
                    (file_index + 1) * 32,
                    (rank + 1) * 32,
                ),
                fill=color,
            )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _runtime_payload(*, mode: str, publishable: bool) -> dict:
    blockers = [] if publishable else ["shadow_mode_not_publishable"]
    return {
        "schema": "kindlemaster.fen_square_runtime.v1",
        "status": "accepted_candidate",
        "mode": mode,
        "placement": PLACEMENT,
        "validation_fen": f"{PLACEMENT} w - - 0 1",
        "confidence": 0.999,
        "candidate_accepted": True,
        "publishable": publishable,
        "blockers": [],
        "publish_blockers": blockers,
        "owning_blocker": blockers[0] if blockers else "",
        "squares": [],
        "provenance": {"model_name": "unit-test"},
    }


class ChessFenRuntimeTests(unittest.TestCase):
    def test_committed_model_loads_with_hash_and_calibration(self) -> None:
        loaded = load_portable_fen_square_model(DEFAULT_FEN_MODEL_PATH)

        self.assertEqual(loaded["status"], "ready")
        self.assertEqual(len(loaded["model"]["classes"]), 13)
        self.assertAlmostEqual(loaded["model"]["acceptance_threshold"], 0.963637, places=6)
        self.assertAlmostEqual(
            loaded["model"]["piece_confidence_threshold"], 0.963604, places=6
        )
        self.assertAlmostEqual(
            loaded["model"]["king_confidence_threshold"], 0.964115, places=6
        )
        self.assertAlmostEqual(
            loaded["model"]["ood_distance_threshold"], 1.412992586716347
        )
        self.assertEqual(
            loaded["provenance"]["artifact_sha256"],
            "95d2a653155ec3168a44f2635c9bc4b150dbf5c087cfe725eaab0f116fab813f",
        )
        self.assertEqual(
            loaded["provenance"]["training_data"]["dataset_sha256"],
            "78e4385aeb1f595171d8370be84afd2573051ab790458c4d819ab29c00466201",
        )
        comparison = loaded["provenance"]["benchmark_comparison"]
        self.assertEqual(comparison["baseline"]["exact_board_accuracy"], 0.0)
        self.assertEqual(comparison["candidate"]["exact_board_accuracy"], 0.703704)

    def test_model_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / "model.npz"
            manifest = root / "model.manifest.json"
            shutil.copy2(DEFAULT_FEN_MODEL_PATH, model)
            source_manifest = DEFAULT_FEN_MODEL_PATH.with_suffix(".manifest.json")
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            payload["artifact_sha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_portable_fen_square_model(model)

        self.assertEqual(loaded["status"], "invalid")
        self.assertEqual(loaded["error"], "model_artifact_hash_mismatch")

    def test_model_manifest_version_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / "model.npz"
            manifest = root / "model.manifest.json"
            shutil.copy2(DEFAULT_FEN_MODEL_PATH, model)
            source_manifest = DEFAULT_FEN_MODEL_PATH.with_suffix(".manifest.json")
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            payload["schema"] = "kindlemaster.fen_square_classifier.portable.v0"
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_portable_fen_square_model(model)

        self.assertEqual(loaded["status"], "invalid")
        self.assertEqual(loaded["error"], "model_manifest_contract_invalid")

    def test_portable_export_rejects_source_manifest_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "candidate.joblib"
            source.write_bytes(b"training-model")

            result = export_portable_fen_square_model(
                source,
                output_path=root / "candidate.npz",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["error"],
            "portable_manifest_path_conflicts_with_source_manifest",
        )

    def test_marker_calibration_loads_source_bound_profile(self) -> None:
        loaded = load_marker_confidence_calibration(
            source_profile="yusupov-fundamentals"
        )

        self.assertEqual(loaded["status"], "ready")
        self.assertEqual(
            loaded["provenance"]["calibration_version"],
            "yusupov-marker-reliability-v1",
        )
        self.assertEqual(loaded["calibration"]["sample_count"], 22)
        self.assertFalse(loaded["calibration"]["holdout_used_for_tuning"])

    def test_portable_predictor_reports_all_squares_and_owning_blocker(self) -> None:
        with Image.open(io.BytesIO(_board_bytes())) as image:
            result = predict_portable_fen_board(
                image,
                model_path=DEFAULT_FEN_MODEL_PATH,
                mode="shadow",
            )

        self.assertIn(result["status"], {"accepted_candidate", "needs_review"})
        self.assertEqual(len(result["squares"]), 64)
        self.assertTrue(result["owning_blocker"])
        self.assertFalse(result["publishable"])
        self.assertGreaterEqual(result["timing"]["total_ms"], 0.0)
        self.assertIn("minimum_runner_up_margin", result)
        self.assertEqual(result["confidence_policy"], "board_evidence_v2")
        self.assertIn("minimum_piece_confidence", result["board_evidence"])
        self.assertIn("minimum_king_confidence", result["board_evidence"])
        self.assertTrue(result["board_evidence"]["ood"]["available"])
        self.assertEqual(result["orientation"]["value"], "white_bottom")

    def test_off_mode_is_exact_runtime_rollback(self) -> None:
        recognition = ChessFenResult(
            placement=PLACEMENT,
            method="template",
            board_detected=True,
            requires_review=True,
        )
        with patch("chess_fen_runtime.predict_portable_fen_board") as predictor:
            result = apply_fen_square_runtime(
                recognition,
                _board_bytes(),
                mode="off",
            )

        self.assertIs(result, recognition)
        predictor.assert_not_called()

    def test_shadow_mode_never_changes_template_output(self) -> None:
        recognition = ChessFenResult(
            fen=f"{PLACEMENT} w - - 0 1",
            placement=PLACEMENT,
            method="template",
            board_detected=True,
            requires_review=False,
        )
        with patch(
            "chess_fen_runtime.predict_portable_fen_board",
            return_value=_runtime_payload(mode="shadow", publishable=False),
        ):
            result = apply_fen_square_runtime(
                recognition,
                _board_bytes(),
                mode="shadow",
            )

        self.assertEqual(result.fen, recognition.fen)
        self.assertFalse(result.requires_review)
        self.assertEqual(result.model_runtime["template_comparison"], "exact")
        self.assertIn("shadow_mode_not_publishable", result.recognition_blockers)

    def test_assist_without_template_consensus_stays_in_review(self) -> None:
        recognition = ChessFenResult(
            placement="",
            method="template",
            board_detected=True,
            requires_review=True,
        )
        with patch(
            "chess_fen_runtime.predict_portable_fen_board",
            return_value=_runtime_payload(mode="assist", publishable=True),
        ):
            result = apply_fen_square_runtime(
                recognition,
                _board_bytes(),
                mode="assist",
            )

        self.assertEqual(result.placement, "")
        self.assertTrue(result.requires_review)
        self.assertFalse(result.model_runtime["publishable"])
        self.assertEqual(result.model_runtime["owning_blocker"], "model_template_consensus_missing")

    def test_assist_exact_template_consensus_can_promote_placement_only(self) -> None:
        recognition = ChessFenResult(
            placement=PLACEMENT,
            method="template",
            board_detected=True,
            requires_review=True,
        )
        with patch(
            "chess_fen_runtime.predict_portable_fen_board",
            return_value=_runtime_payload(mode="assist", publishable=True),
        ):
            result = apply_fen_square_runtime(recognition, _board_bytes(), mode="assist")

        payload = result.to_dict()
        self.assertEqual(result.placement, PLACEMENT)
        self.assertEqual(result.side_to_move_status, "inferred")
        self.assertTrue(result.requires_review)
        self.assertEqual(payload["fen"], "")
        self.assertEqual(payload["full_fen"], "")
        self.assertEqual(payload["fen_suppressed_reason"], "side_to_move_inferred")

    def test_assist_keeps_any_model_template_conflict_in_review(self) -> None:
        recognition = ChessFenResult(
            placement="8/8/8/8/8/8/8/4K2k",
            method="template",
            board_detected=True,
            requires_review=True,
        )
        with patch(
            "chess_fen_runtime.predict_portable_fen_board",
            return_value=_runtime_payload(mode="assist", publishable=True),
        ):
            result = apply_fen_square_runtime(
                recognition,
                _board_bytes(),
                mode="assist",
            )

        self.assertEqual(result.placement, recognition.placement)
        self.assertTrue(result.requires_review)
        self.assertFalse(result.model_runtime["publishable"])
        self.assertEqual(
            result.model_runtime["owning_blocker"],
            "model_template_conflict",
        )

    def test_summary_attributes_model_status_and_blocker(self) -> None:
        result = ChessFenResult(
            placement=PLACEMENT,
            board_detected=True,
            requires_review=True,
            model_runtime=_runtime_payload(mode="shadow", publishable=False),
            recognition_blockers=["shadow_mode_not_publishable"],
        )

        summary = summarize_chess_fen_results([result.to_dict()])

        self.assertEqual(summary["model_runtime_count"], 1)
        self.assertEqual(summary["model_accepted_candidate_count"], 1)
        self.assertEqual(
            summary["model_owning_blocker_counts"],
            {"shadow_mode_not_publishable": 1},
        )

    def test_conversion_config_honors_single_environment_rollback(self) -> None:
        with patch.dict(
            "os.environ",
            {"KINDLEMASTER_CHESS_FEN_MODEL_MODE": "off"},
            clear=False,
        ):
            config = ConversionConfig()

        self.assertEqual(config.chess_fen_model_mode, "off")

    def test_web_conversion_enables_shadow_by_default(self) -> None:
        request = ConversionRequest(
            source_path="book.pdf",
            original_filename="book.pdf",
            profile="auto-premium",
            language="pl",
        )
        with patch.dict("os.environ", {}, clear=True):
            config = build_conversion_config(request)

        self.assertEqual(config.chess_fen_model_mode, "shadow")

    def test_extractor_routes_image_recognition_through_shared_runtime(self) -> None:
        recognition = ChessFenResult(
            placement=PLACEMENT,
            board_detected=True,
            requires_review=True,
        )
        config = ConversionConfig(chess_fen_model_mode="shadow")
        with (
            patch(
                "pymupdf_chess_extractor.recognize_chess_position_from_image",
                return_value=recognition,
            ) as template_recognizer,
            patch(
                "pymupdf_chess_extractor.apply_fen_square_runtime",
                return_value=recognition,
            ) as model_runtime,
        ):
            result = _recognize_chess_position_with_runtime(
                _board_bytes(),
                bbox=(0.0, 0.0, 256.0, 256.0),
                min_confidence=0.835,
                piece_templates={},
                config=config,
            )

        self.assertIs(result, recognition)
        template_recognizer.assert_called_once()
        model_runtime.assert_called_once()

    def test_extractor_skips_square_model_when_board_was_not_localized(self) -> None:
        recognition = ChessFenResult(
            board_detected=False,
            requires_review=True,
        )
        config = ConversionConfig(chess_fen_model_mode="shadow")
        with (
            patch(
                "pymupdf_chess_extractor.recognize_chess_position_from_image",
                return_value=recognition,
            ),
            patch(
                "pymupdf_chess_extractor.apply_fen_square_runtime",
            ) as model_runtime,
        ):
            result = _recognize_chess_position_with_runtime(
                _board_bytes(),
                bbox=(0.0, 0.0, 256.0, 256.0),
                min_confidence=0.835,
                piece_templates={},
                config=config,
            )

        self.assertIs(result, recognition)
        model_runtime.assert_not_called()

    def test_marker_classifier_loads_calibration_only_in_runtime_scope(self) -> None:
        classifier_result = {
            "status": "trusted_marker",
            "side": "w",
            "side_to_move": "w",
            "confidence": 0.8571,
            "classifier_version": "marker_adaptive_v3",
        }
        with (
            fen_runtime_scope(ConversionConfig(chess_fen_model_mode="shadow")),
            patch(
                "chess_marker_classifier_adaptive.classify_marker_crop_adaptive",
                return_value=dict(classifier_result),
            ) as classifier,
        ):
            result = classify_scan_chess_side_marker_crop(
                Image.new("L", (64, 64), 255)
            )

        calibration = classifier.call_args.kwargs["confidence_calibration"]
        self.assertEqual(calibration["source_split"], "calibration")
        self.assertEqual(result["calibration_runtime_status"], "ready")
        self.assertEqual(
            result["calibration_version"],
            "yusupov-marker-reliability-v1",
        )

    def test_invalid_marker_calibration_cannot_produce_trusted_side(self) -> None:
        classifier_result = {
            "status": "trusted_marker",
            "side": "b",
            "side_to_move": "b",
            "confidence": 0.99,
            "classifier_version": "marker_adaptive_v3",
        }
        with (
            patch(
                "pymupdf_chess_extractor.current_marker_runtime_calibration",
                return_value={
                    "status": "invalid",
                    "error": "marker_calibration_artifact_hash_mismatch",
                    "provenance": {},
                },
            ),
            patch(
                "chess_marker_classifier_adaptive.classify_marker_crop_adaptive",
                return_value=dict(classifier_result),
            ),
        ):
            result = classify_scan_chess_side_marker_crop(
                Image.new("L", (64, 64), 255)
            )

        self.assertEqual(result["status"], "side_to_move_marker_local_ambiguous")
        self.assertEqual(result["side"], "")
        self.assertEqual(
            result["calibration_blocker"],
            "marker_calibration_artifact_hash_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
