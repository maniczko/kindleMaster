from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from chess_two_crop_performance import (
    build_two_crop_performance_report,
    build_two_crop_semantic_digest,
    write_two_crop_performance_reports,
)
from pymupdf_chess_extractor import (
    _scan_chess_tight_board_box_in_crop,
    _scan_chess_two_crop_review_artifacts,
)


class ChessTwoCropPerformanceTests(unittest.TestCase):
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

    def test_runtime_instrumentation_detects_repeated_localization_and_split_timings(self) -> None:
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
        self.assertEqual(localization.call_count, 2)
        self.assertEqual(performance["tight_board_localization_call_count"], 2)
        self.assertGreaterEqual(performance["png_encoded_artifact_count"], 3)
        self.assertEqual(performance["png_encoded_artifact_count"], len(files))
        self.assertGreater(performance["png_encoded_bytes"], 0)
        self.assertGreaterEqual(performance["localization_seconds"], 0.0)
        self.assertGreaterEqual(performance["marker_analysis_seconds"], 0.0)
        self.assertGreaterEqual(performance["png_encoding_seconds"], 0.0)
        self.assertFalse(performance["file_write_measured"])

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
            self.assertEqual(report["summary"]["tight_board_localization_call_count"], 4)
            self.assertEqual(report["summary"]["sliding_window_candidate_evaluations"], 404)
            self.assertEqual(report["summary"]["artifact_count"], 2)
            self.assertEqual(report["summary"]["artifact_bytes"], len(b"board-one") + len(b"board-two-longer"))
            self.assertEqual(report["stage_timings"]["localization_seconds"]["median"], 2.0)
            self.assertEqual(report["stage_timings"]["localization_seconds"]["p95"], 3.0)
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
            "tight_board_localization_call_count": 2,
            "sliding_window_candidate_evaluations": candidates,
            "localization_seconds": seconds,
            "marker_analysis_seconds": seconds / 2,
            "png_encoding_seconds": seconds / 4,
            "png_encoded_artifact_count": 1,
            "png_encoded_bytes": 10,
            "file_write_measured": True,
            "file_write_seconds": seconds / 10,
            "file_written_artifact_count": 1,
            "file_written_bytes": 10,
            "total_seconds": seconds * 2,
        },
    }


if __name__ == "__main__":
    unittest.main()
