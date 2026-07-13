from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from chess_two_crop_performance import (
    build_two_crop_performance_report,
    build_two_crop_semantic_digest,
    write_two_crop_performance_reports,
)
from pymupdf_chess_extractor import (
    _scan_chess_board_crop_quality,
    _scan_chess_grid_line_board_analysis,
    _scan_chess_implicit_full_grid_analysis,
    _scan_chess_tight_board_box_in_crop,
    _scan_chess_two_crop_review_artifacts,
)


class ChessTwoCropPerformanceTests(unittest.TestCase):
    def test_confident_full_grid_skips_sliding_window_with_identical_bbox_result(self) -> None:
        image = _full_grid_image()
        fast_performance: dict[str, object] = {}
        legacy_performance: dict[str, object] = {}

        with patch("pymupdf_chess_extractor._scan_chess_board_square_score", return_value=0.0):
            fast_result = _scan_chess_tight_board_box_in_crop(
                image,
                performance=fast_performance,
            )
            legacy_result = _scan_chess_tight_board_box_in_crop(
                image,
                performance=legacy_performance,
                allow_full_grid_fast_path=False,
            )

        self.assertEqual(fast_result, legacy_result)
        self.assertIsNone(fast_result)
        self.assertEqual(fast_performance["localization_path"], "full_grid_fast_path")
        self.assertEqual(fast_performance["localization_reason_codes"], ["full_grid_confident"])
        self.assertEqual(fast_performance["sliding_window_candidate_evaluations"], 0)
        self.assertEqual(fast_performance["full_grid_fast_path_count"], 1)
        self.assertGreater(legacy_performance["sliding_window_candidate_evaluations"], 0)
        self.assertEqual(legacy_performance["localization_path"], "sliding_window_fallback")

    def test_extra_grid_line_and_low_resolution_fail_closed_to_fallback(self) -> None:
        extra_line = _full_grid_image(extra_vertical_line=True)
        low_resolution = _full_grid_image(size=65, spacing=8)

        extra_analysis = _scan_chess_grid_line_board_analysis(extra_line)
        low_analysis = _scan_chess_grid_line_board_analysis(low_resolution)
        extra_performance: dict[str, object] = {}
        low_performance: dict[str, object] = {}
        with patch("pymupdf_chess_extractor._scan_chess_board_square_score", return_value=0.0):
            _scan_chess_tight_board_box_in_crop(extra_line, performance=extra_performance)
            _scan_chess_tight_board_box_in_crop(low_resolution, performance=low_performance)

        self.assertFalse(extra_analysis["full_grid_confident"])
        self.assertIn("vertical_grid_line_count_not_nine", extra_analysis["reason_codes"])
        self.assertEqual(extra_performance["localization_path"], "sliding_window_fallback")
        self.assertGreater(extra_performance["sliding_window_candidate_evaluations"], 0)
        self.assertFalse(low_analysis["full_grid_confident"])
        self.assertIn("full_grid_resolution_below_minimum", low_analysis["reason_codes"])
        self.assertEqual(low_performance["localization_path"], "sliding_window_fallback")

    def test_implicit_periodic_grid_fast_path_requires_no_better_inset(self) -> None:
        full_grid = _implicit_grid_image(size=256, inset=8)
        partial_grid = _implicit_grid_image(size=256, inset=16)
        performance: dict[str, object] = {}

        full_analysis = _scan_chess_implicit_full_grid_analysis(full_grid)
        partial_analysis = _scan_chess_implicit_full_grid_analysis(partial_grid)
        result = _scan_chess_tight_board_box_in_crop(full_grid, performance=performance)

        self.assertTrue(full_analysis["full_grid_confident"])
        self.assertEqual(full_analysis["reason_codes"], ["implicit_full_grid_confident"])
        self.assertEqual(
            full_analysis["metrics"]["evidence_type"],
            "implicit_8x8_periodicity",
        )
        self.assertIsNone(result)
        self.assertEqual(performance["localization_path"], "full_grid_fast_path")
        self.assertEqual(
            performance["localization_reason_codes"],
            ["implicit_full_grid_confident"],
        )
        self.assertEqual(performance["sliding_window_candidate_evaluations"], 0)
        self.assertGreater(performance["full_grid_probe_evaluations"], 0)
        self.assertFalse(partial_analysis["full_grid_confident"])
        self.assertIn("implicit_grid_inset_candidate_gain", partial_analysis["reason_codes"])

    def test_generated_fixture_classes_produce_stable_semantic_digest(self) -> None:
        records = []
        with patch("pymupdf_chess_extractor._scan_chess_tight_board_box_in_crop", return_value=None):
            for name, image, board_bbox in _generated_fixture_cases():
                fields, _files = _scan_chess_two_crop_review_artifacts(
                    image,
                    filename=f"{name}.png",
                    board_bbox=board_bbox,
                    side_marker_bbox=None,
                )
                records.append({"diagram_id": name, "page": 1, **fields})

        changed_timings = [
            {
                **record,
                "two_crop_performance": {
                    **record["two_crop_performance"],
                    "total_seconds": 999.0,
                },
            }
            for record in records
        ]

        self.assertEqual(
            {record["diagram_id"] for record in records},
            {"tight_board", "board_with_margin", "marker_present", "marker_missing", "neighboring_board"},
        )
        self.assertEqual(
            build_two_crop_semantic_digest(records),
            build_two_crop_semantic_digest(changed_timings),
        )
        self.assertEqual(
            build_two_crop_semantic_digest(records),
            build_two_crop_semantic_digest(list(reversed(records))),
        )

    def test_runtime_uses_single_localization_and_split_timings(self) -> None:
        _name, image, board_bbox = _generated_fixture_cases()[0]
        with (
            patch("pymupdf_chess_extractor._scan_chess_board_square_score", return_value=0.0),
            patch(
                "pymupdf_chess_extractor._scan_chess_tight_board_box_in_crop",
                wraps=_scan_chess_tight_board_box_in_crop,
            ) as localization,
        ):
            fields, files = _scan_chess_two_crop_review_artifacts(
                image,
                filename="instrumented.png",
                board_bbox=board_bbox,
                side_marker_bbox=None,
            )

        performance = fields["two_crop_performance"]
        self.assertEqual(localization.call_count, 1)
        self.assertEqual(performance["tight_board_localization_call_count"], 1)
        self.assertEqual(performance["board_analysis_mode"], "single_pass")
        self.assertFalse(performance["legacy_localization_fallback_used"])
        self.assertGreaterEqual(performance["png_encoded_artifact_count"], 3)
        self.assertEqual(performance["png_encoded_artifact_count"], len(files))
        self.assertGreater(performance["png_encoded_bytes"], 0)
        self.assertGreaterEqual(performance["localization_seconds"], 0.0)
        self.assertGreaterEqual(performance["marker_analysis_seconds"], 0.0)
        self.assertGreaterEqual(performance["png_encoding_seconds"], 0.0)
        self.assertFalse(performance["file_write_measured"])

    def test_single_pass_semantics_match_legacy_quality_for_all_fixture_classes(self) -> None:
        single_pass_records = []
        legacy_records = []
        for name, image, board_bbox in _generated_fixture_cases():
            fields, _files = _scan_chess_two_crop_review_artifacts(
                image,
                filename=f"{name}.png",
                board_bbox=board_bbox,
                side_marker_bbox=None,
            )
            legacy_quality = _scan_chess_board_crop_quality(image, fields["board_bbox"])
            legacy_fields = deepcopy(fields)
            legacy_fields["board_crop_quality_gate"] = legacy_quality
            legacy_fields["board_crop_quality"] = (
                "pass" if legacy_quality.get("decision") == "pass" else "fail"
            )
            legacy_fields["board_crop_fail_reason"] = list(legacy_quality.get("reasons") or [])
            single_pass_records.append({"diagram_id": name, "page": 1, **fields})
            legacy_records.append({"diagram_id": name, "page": 1, **legacy_fields})

        self.assertEqual(
            build_two_crop_semantic_digest(single_pass_records),
            build_two_crop_semantic_digest(legacy_records),
        )

    def test_ambiguous_single_pass_uses_reported_legacy_fallback(self) -> None:
        _name, image, board_bbox = _generated_fixture_cases()[0]

        def localized_box(_image, *, performance=None):
            if performance is not None:
                performance["tight_board_localization_call_count"] = int(
                    performance.get("tight_board_localization_call_count") or 0
                ) + 1
            return (0, 0, 96, 96)

        with (
            patch(
                "pymupdf_chess_extractor._scan_chess_tight_board_box_in_crop",
                side_effect=localized_box,
            ) as localization,
            patch(
                "pymupdf_chess_extractor._scan_chess_single_pass_needs_fallback",
                return_value=True,
            ),
        ):
            fields, _files = _scan_chess_two_crop_review_artifacts(
                image,
                filename="fallback.png",
                board_bbox=board_bbox,
                side_marker_bbox=None,
            )

        performance = fields["two_crop_performance"]
        self.assertEqual(localization.call_count, 2)
        self.assertEqual(performance["tight_board_localization_call_count"], 2)
        self.assertEqual(performance["board_analysis_mode"], "legacy_fallback")
        self.assertTrue(performance["legacy_localization_fallback_used"])
        self.assertEqual(performance["legacy_localization_fallback_count"], 1)
        self.assertEqual(
            performance["legacy_localization_fallback_reason"],
            "residual_board_candidate_gain",
        )

    def test_job_output_report_aggregates_timings_candidates_artifacts_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            report_root = job / "reports" / "chess_fen"
            report_root.mkdir(parents=True)
            artifact_root = job / "review" / "chess_fen" / "two_crop"
            artifact_root.mkdir(parents=True)
            (artifact_root / "one_board.png").write_bytes(b"board-one")
            (artifact_root / "two_board.png").write_bytes(b"board-two-longer")
            rows = [
                _runtime_row("one", 1.0, 101, "review/chess_fen/two_crop/one_board.png"),
                _runtime_row("two", 3.0, 303, "review/chess_fen/two_crop/two_board.png"),
            ]
            (report_root / "two_crop_quality_metrics.json").write_text(
                json.dumps({"items": rows}, ensure_ascii=False),
                encoding="utf-8",
            )
            (report_root / "source_manifest.json").write_text(
                json.dumps({"source_sha256": "abc123", "corpus_enforced": True}),
                encoding="utf-8",
            )

            report = build_two_crop_performance_report(job)
            json_path, markdown_path = write_two_crop_performance_reports(report, root / "reports")

            self.assertEqual(report["status"], "ok")
            self.assertTrue(report["evidence"]["corpus_available"])
            self.assertTrue(report["evidence"]["corpus_complete"])
            self.assertTrue(report["evidence"]["corpus_enforced"])
            self.assertEqual(report["evidence"]["source_sha256"], "abc123")
            self.assertFalse(report["evidence"]["synthetic_substitution_used"])
            self.assertEqual(report["summary"]["runtime_record_count"], 2)
            self.assertEqual(report["summary"]["instrumented_record_count"], 2)
            self.assertEqual(report["summary"]["tight_board_localization_call_count"], 3)
            self.assertEqual(report["summary"]["sliding_window_candidate_evaluations"], 404)
            self.assertEqual(
                report["summary"]["localization_paths"],
                {"full_grid_fast_path": 1, "sliding_window_fallback": 1},
            )
            self.assertEqual(report["summary"]["full_grid_fast_path_count"], 1)
            self.assertEqual(report["summary"]["full_grid_fallback_count"], 1)
            self.assertEqual(report["summary"]["full_grid_fast_path_coverage_rate"], 0.5)
            self.assertEqual(report["summary"]["full_grid_fallback_rate"], 0.5)
            self.assertEqual(report["summary"]["full_grid_probe_evaluations"], 15)
            self.assertEqual(report["summary"]["false_fast_path_count"], 0)
            self.assertEqual(report["summary"]["single_pass_record_count"], 1)
            self.assertEqual(report["summary"]["legacy_fallback_record_count"], 1)
            self.assertEqual(
                report["summary"]["legacy_fallback_reasons"],
                {"residual_board_candidate_gain": 1},
            )
            self.assertEqual(report["summary"]["ambiguity_probe_evaluations"], 20)
            self.assertEqual(report["summary"]["artifact_count"], 2)
            self.assertEqual(report["summary"]["artifact_bytes"], len(b"board-one") + len(b"board-two-longer"))
            self.assertEqual(report["stage_timings"]["localization_seconds"]["median"], 2.0)
            self.assertEqual(report["stage_timings"]["localization_seconds"]["p95"], 3.0)
            self.assertEqual(
                report["localization_path_timings"]["full_grid_fast_path"]["median"],
                1.0,
            )
            self.assertEqual(
                report["localization_path_timings"]["sliding_window_fallback"]["median"],
                3.0,
            )
            self.assertTrue(report["summary"]["semantic_digest"])
            self.assertTrue(json_path.is_file())
            self.assertIn("Stage Timings", markdown_path.read_text(encoding="utf-8"))

    def test_missing_job_output_cannot_claim_real_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = build_two_crop_performance_report(Path(temp_dir) / "missing")

        self.assertEqual(report["status"], "corpus_unavailable")
        self.assertFalse(report["evidence"]["corpus_available"])
        self.assertFalse(report["evidence"]["corpus_enforced"])
        self.assertFalse(report["evidence"]["synthetic_substitution_used"])
        self.assertEqual(report["summary"]["runtime_record_count"], 0)

    def test_kindlemaster_cli_accepts_job_output_and_writes_safe_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            report_root = job / "reports" / "chess_fen"
            report_root.mkdir(parents=True)
            (report_root / "two_crop_quality_metrics.json").write_text(
                json.dumps({"items": [_runtime_row("one", 1.0, 10, "")]}),
                encoding="utf-8",
            )
            out = root / "performance"
            completed = subprocess.run(
                [
                    sys.executable,
                    "kindlemaster.py",
                    "chess-study",
                    "two-crop-performance",
                    "--job-output",
                    str(job),
                    "--report-dir",
                    str(out),
                ],
                cwd=Path(__file__).resolve().parent,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads((out / "baseline.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["runtime_record_count"], 1)
            self.assertTrue((out / "baseline.md").is_file())


def _generated_fixture_cases() -> list[tuple[str, Image.Image, tuple[int, int, int, int]]]:
    cases = []
    cases.append(("tight_board", *_board_image(canvas=128, board=(16, 16, 112, 112))))
    margin_image, _ = _board_image(canvas=144, board=(24, 24, 120, 120))
    cases.append(("board_with_margin", margin_image, (12, 12, 132, 132)))
    marker_image, marker_bbox = _board_image(canvas=160, board=(16, 32, 112, 128), marker="outline")
    cases.append(("marker_present", marker_image, marker_bbox))
    missing_image, missing_bbox = _board_image(canvas=160, board=(16, 32, 112, 128))
    cases.append(("marker_missing", missing_image, missing_bbox))
    neighbor = Image.new("RGB", (272, 144), "white")
    first, first_bbox = _board_image(canvas=128, board=(16, 16, 112, 112))
    second, _second_bbox = _board_image(canvas=128, board=(16, 16, 112, 112))
    neighbor.paste(first, (0, 8))
    neighbor.paste(second, (136, 8))
    cases.append(("neighboring_board", neighbor, tuple(value + (8 if index % 2 else 0) for index, value in enumerate(first_bbox))))
    return cases


def _full_grid_image(
    *,
    size: int = 97,
    spacing: int = 12,
    extra_vertical_line: bool = False,
) -> Image.Image:
    image = Image.new("L", (size, size), "white")
    draw = ImageDraw.Draw(image)
    coordinates = [index * spacing for index in range(9)]
    for coordinate in coordinates:
        draw.line((coordinate, 0, coordinate, size - 1), fill="black", width=1)
        draw.line((0, coordinate, size - 1, coordinate), fill="black", width=1)
    if extra_vertical_line:
        draw.line((spacing // 2, 0, spacing // 2, size - 1), fill="black", width=1)
    return image.convert("RGB")


def _implicit_grid_image(*, size: int, inset: int) -> Image.Image:
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    board_side = size - inset * 2
    cell = board_side // 8
    for rank in range(8):
        for file_index in range(8):
            left = inset + file_index * cell
            top = inset + rank * cell
            color = "#444444" if (rank + file_index) % 2 else "#eeeeee"
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=color)
    draw.rectangle((inset, inset, size - inset - 1, size - inset - 1), outline="black", width=2)
    return image


def _board_image(
    *,
    canvas: int,
    board: tuple[int, int, int, int],
    marker: str = "",
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    image = Image.new("RGB", (canvas, canvas), "white")
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = board
    cell = (x1 - x0) // 8
    for rank in range(8):
        for file_index in range(8):
            color = "#444444" if (rank + file_index) % 2 else "#eeeeee"
            left = x0 + file_index * cell
            top = y0 + rank * cell
            draw.rectangle((left, top, left + cell, top + cell), fill=color)
    draw.rectangle(board, outline="black", width=2)
    if marker == "outline":
        draw.polygon(((124, 54), (116, 70), (132, 70)), outline="black")
    return image, board


def _runtime_row(diagram_id: str, seconds: float, candidates: int, board_path: str) -> dict[str, object]:
    fallback = diagram_id == "two"
    return {
        "diagram_id": diagram_id,
        "page": 1,
        "board_bbox": [1, 2, 101, 102],
        "board_crop_path": board_path,
        "board_crop_quality": "pass",
        "board_crop_fail_reason": [],
        "marker_crop_quality": "fail",
        "marker_crop_fail_reason": ["marker_missing"],
        "side_marker_status": "marker_missing",
        "side_to_move": "unknown",
        "manual_review_required": True,
        "manual_review_reason": "marker_missing",
        "two_crop_performance": {
            "tight_board_localization_call_count": 2 if fallback else 1,
            "sliding_window_candidate_evaluations": candidates,
            "localization_path": (
                "sliding_window_fallback" if fallback else "full_grid_fast_path"
            ),
            "localization_reason_codes": (
                ["residual_board_candidate_gain"] if fallback else ["full_grid_confident"]
            ),
            "full_grid_fast_path_count": 0 if fallback else 1,
            "full_grid_fallback_count": 1 if fallback else 0,
            "full_grid_probe_evaluations": 0 if fallback else 15,
            "false_fast_path_count": 0,
            "localization_seconds": seconds,
            "marker_analysis_seconds": seconds / 2,
            "png_encoding_seconds": seconds / 4,
            "png_encoded_artifact_count": 1,
            "png_encoded_bytes": 10,
            "file_write_measured": True,
            "file_write_seconds": seconds / 10,
            "file_written_artifact_count": 1,
            "file_written_bytes": 10,
            "board_analysis_mode": "legacy_fallback" if fallback else "single_pass",
            "legacy_localization_fallback_used": fallback,
            "legacy_localization_fallback_count": 1 if fallback else 0,
            "legacy_localization_fallback_reason": (
                "residual_board_candidate_gain" if fallback else ""
            ),
            "ambiguity_probe_evaluations": 10,
            "ambiguity_probe_seconds": seconds / 20,
            "total_seconds": seconds * 2,
        },
    }


if __name__ == "__main__":
    unittest.main()
