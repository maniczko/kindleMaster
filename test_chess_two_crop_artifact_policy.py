from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from chess_two_crop_performance import build_two_crop_semantic_digest
from pymupdf_chess_extractor import _scan_chess_two_crop_review_artifacts


class ChessTwoCropArtifactPolicyTests(unittest.TestCase):
    def test_policies_preserve_required_artifacts_and_semantics(self) -> None:
        image = _fixture_image()
        records = {}
        files_by_policy = {}
        with _stable_quality_runtime():
            for policy in ("all", "blockers", "none"):
                fields, files = _scan_chess_two_crop_review_artifacts(
                    image,
                    filename="clean.png",
                    board_bbox=[20, 20, 220, 220],
                    side_marker_bbox=[222, 100, 232, 112],
                    debug_artifact_policy=policy,
                )
                records[policy] = {"diagram_id": "same", "page": 1, **fields}
                files_by_policy[policy] = files

        digests = {
            build_two_crop_semantic_digest([record]) for record in records.values()
        }
        self.assertEqual(len(digests), 1)
        required_paths = {
            item["path"]
            for item in files_by_policy["all"]
            if item.get("artifact_class") == "required"
        }
        self.assertGreaterEqual(len(required_paths), 3)
        for policy in ("all", "blockers", "none"):
            self.assertEqual(
                required_paths,
                {
                    item["path"]
                    for item in files_by_policy[policy]
                    if item.get("artifact_class") == "required"
                },
            )
            emitted_paths = {item["path"] for item in files_by_policy[policy]}
            for key in (
                "board_crop_path",
                "side_marker_crop_path",
                "side_marker_search_crop_path",
                "debug_overlay_path",
                "debug_context_crop_path",
            ):
                path = str(records[policy].get(key) or "")
                if path:
                    self.assertIn(path, emitted_paths)

        self.assertGreater(
            records["all"]["two_crop_performance"]["optional_png_encoded_artifact_count"],
            0,
        )
        for policy in ("blockers", "none"):
            performance = records[policy]["two_crop_performance"]
            self.assertEqual(performance["artifact_policy"], policy)
            self.assertEqual(performance["optional_png_encoded_artifact_count"], 0)
            self.assertGreater(performance["optional_debug_skipped_count"], 0)
            self.assertEqual(records[policy]["debug_overlay_path"], "")
            self.assertEqual(records[policy]["debug_context_crop_path"], "")

    def test_blockers_emits_optional_debug_but_none_never_does(self) -> None:
        image = _fixture_image()
        with _stable_quality_runtime():
            blockers_fields, blockers_files = _scan_chess_two_crop_review_artifacts(
                image,
                filename="blocked.png",
                board_bbox=[20, 20, 220, 220],
                side_marker_bbox=[222, 100, 232, 112],
                debug_artifact_policy="blockers",
                blocker_context={"acceptance_blocker_codes": ["placement_review_required"]},
            )
            none_fields, none_files = _scan_chess_two_crop_review_artifacts(
                image,
                filename="blocked.png",
                board_bbox=[20, 20, 220, 220],
                side_marker_bbox=[222, 100, 232, 112],
                debug_artifact_policy="none",
                blocker_context={"acceptance_blocker_codes": ["placement_review_required"]},
            )

        self.assertTrue(
            any(item.get("artifact_class") == "optional" for item in blockers_files)
        )
        self.assertFalse(any(item.get("artifact_class") == "optional" for item in none_files))
        self.assertGreater(
            blockers_fields["two_crop_performance"]["optional_png_encoded_bytes"],
            none_fields["two_crop_performance"]["optional_png_encoded_bytes"],
        )

    def test_invalid_policy_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported chess debug artifact policy"):
            _scan_chess_two_crop_review_artifacts(
                _fixture_image(),
                filename="invalid.png",
                board_bbox=[20, 20, 220, 220],
                side_marker_bbox=None,
                debug_artifact_policy="sometimes",
            )

    def test_blocker_policy_recognizes_failures_and_errors(self) -> None:
        image = _fixture_image()
        with _stable_quality_runtime():
            for status in ("FAIL", "FAILURE", "FAILED", "ERROR"):
                with self.subTest(status=status):
                    fields, files = _scan_chess_two_crop_review_artifacts(
                        image,
                        filename=f"status-{status}.png",
                        board_bbox=[20, 20, 220, 220],
                        side_marker_bbox=[222, 100, 232, 112],
                        debug_artifact_policy="blockers",
                        blocker_context={"placement_status": status},
                    )
                    self.assertTrue(
                        any(item.get("artifact_class") == "optional" for item in files)
                    )
                    self.assertGreater(
                        fields["two_crop_performance"]["optional_png_encoded_artifact_count"],
                        0,
                    )


def _fixture_image() -> Image.Image:
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    cell = 25
    for rank in range(8):
        for file_index in range(8):
            color = "#5f7f5f" if (rank + file_index) % 2 else "#e8dfc5"
            left = 20 + file_index * cell
            top = 20 + rank * cell
            draw.rectangle((left, top, left + cell, top + cell), fill=color)
    draw.ellipse((222, 100, 232, 112), fill="black")
    return image


def _stable_quality_runtime():
    return _PatchStack(
        patch(
            "pymupdf_chess_extractor._scan_chess_tight_board_box_in_crop",
            return_value=(10, 10, 190, 190),
        ),
        patch(
            "pymupdf_chess_extractor._scan_chess_board_crop_quality",
            return_value={"decision": "pass", "reasons": []},
        ),
        patch(
            "pymupdf_chess_extractor._scan_chess_marker_crop_quality",
            return_value={
                "decision": "pass",
                "reasons": [],
                "classifier_version": "test",
                "reason": "trusted",
                "confidence": 0.99,
                "symbol": "black",
                "side_to_move": "black",
            },
        ),
    )


class _PatchStack:
    def __init__(self, *patchers):
        self.patchers = patchers

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for patcher in reversed(self.patchers):
            patcher.stop()
        return False


if __name__ == "__main__":
    unittest.main()
