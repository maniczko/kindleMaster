from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import fitz

import kindlemaster
from chess_diagram_fingerprint import DIAGRAM_FINGERPRINT_SCHEMA, source_document_sha256
from chess_study_export import (
    _attach_pdf_side_marker_evidence_to_study_diagrams,
    _study_two_crop_quality_rows,
)
from chess_two_crop_checkpoint import (
    atomic_write_checkpoint,
    build_checkpoint_identity,
    checkpoint_path,
    load_compatible_checkpoint,
    new_checkpoint,
)


class ChessTwoCropCheckpointTests(unittest.TestCase):
    def test_atomic_checkpoint_and_all_identity_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = checkpoint_path(root)
            identity = _identity()
            payload = new_checkpoint(
                identity,
                total_pages=2,
                total_diagrams=2,
                resume_requested=False,
                resume_reason_code="resume_not_requested",
            )
            atomic_write_checkpoint(path, payload)

            self.assertEqual(load_compatible_checkpoint(path, identity).reason_code, "checkpoint_compatible")
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])
            for key, replacement in {
                "source_pdf_sha256": "b" * 64,
                "fingerprint_version": "kindlemaster.chess.diagram_fingerprint.v999",
                "fingerprint_schema": "kindlemaster.chess.diagram_fingerprint.v999",
                "algorithm_version": "other-algorithm",
                "crop_version": "other-crop",
                "dpi": 999,
                "quality_profile": "other-profile",
            }.items():
                mismatched = dict(identity)
                mismatched[key] = replacement
                result = load_compatible_checkpoint(path, mismatched)
                self.assertFalse(result.compatible)
                self.assertEqual(result.reason_code, f"checkpoint_{key}_mismatch")

            path.write_text("{broken", encoding="utf-8")
            corrupt = load_compatible_checkpoint(path, identity)
            self.assertFalse(corrupt.compatible)
            self.assertEqual(corrupt.reason_code, "checkpoint_corrupt")

    def test_interrupt_resume_matches_uninterrupted_without_recomputing_completed_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            _make_pdf(pdf_path, pages=2)
            source_hash = source_document_sha256(pdf_path)
            interrupted_out = root / "interrupted"
            interrupted_diagrams = _diagrams(source_hash)
            interrupted_calls: list[str] = []

            def interrupted_scan(_image, *, filename, **_kwargs):
                interrupted_calls.append(filename)
                if len(interrupted_calls) == 2:
                    raise RuntimeError("synthetic interruption")
                return _two_crop_fields(filename), []

            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                with _patched_two_crop_runtime(interrupted_scan):
                    _attach_pdf_side_marker_evidence_to_study_diagrams(
                        pdf_path,
                        interrupted_diagrams,
                        interrupted_out,
                        dpi=96,
                        min_confidence=0.5,
                        quality_profile="default",
                        resume=False,
                    )

            partial = json.loads(checkpoint_path(interrupted_out).read_text(encoding="utf-8"))
            self.assertEqual(partial["completed_pages"], [1])
            self.assertEqual(partial["completed_diagram_count"], 1)

            resumed_diagrams = _diagrams(source_hash)
            resumed_calls: list[str] = []

            def resumed_scan(_image, *, filename, **_kwargs):
                resumed_calls.append(filename)
                return _two_crop_fields(filename), []

            console = io.StringIO()
            with _patched_two_crop_runtime(resumed_scan), redirect_stdout(console):
                resumed_summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                    pdf_path,
                    resumed_diagrams,
                    interrupted_out,
                    dpi=96,
                    min_confidence=0.5,
                    quality_profile="default",
                    resume=True,
                )

            uninterrupted_out = root / "uninterrupted"
            uninterrupted_diagrams = _diagrams(source_hash)
            uninterrupted_calls: list[str] = []

            def uninterrupted_scan(_image, *, filename, **_kwargs):
                uninterrupted_calls.append(filename)
                return _two_crop_fields(filename), []

            with _patched_two_crop_runtime(uninterrupted_scan):
                uninterrupted_summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                    pdf_path,
                    uninterrupted_diagrams,
                    uninterrupted_out,
                    dpi=96,
                    min_confidence=0.5,
                    quality_profile="default",
                    resume=False,
                )

            self.assertEqual(resumed_calls, ["second.png"])
            self.assertEqual(uninterrupted_calls, ["first.png", "second.png"])
            self.assertEqual(_semantic_results(resumed_diagrams), _semantic_results(uninterrupted_diagrams))
            self.assertEqual(
                _study_two_crop_quality_rows(resumed_diagrams),
                _study_two_crop_quality_rows(uninterrupted_diagrams),
            )
            self.assertTrue(resumed_summary["resume_used"])
            self.assertEqual(resumed_summary["reused_diagram_count"], 1)
            self.assertEqual(resumed_summary["computed_diagram_count"], 1)
            self.assertFalse(uninterrupted_summary["resume_used"])
            self.assertEqual(uninterrupted_summary["computed_diagram_count"], 2)
            self.assertIn("mode=reused", console.getvalue())
            self.assertIn("progress=100.00%", console.getvalue())
            completed = json.loads(checkpoint_path(interrupted_out).read_text(encoding="utf-8"))
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["progress_percent"], 100.0)
            serialized_checkpoint = json.dumps(completed)
            self.assertNotIn(str(pdf_path), serialized_checkpoint)
            self.assertNotIn("data:image", serialized_checkpoint)
            self.assertFalse(
                any(
                    sensitive in str(key).lower()
                    for sensitive in ("credential", "password", "secret", ".env")
                    for key in completed
                )
            )

    def test_cold_mismatch_and_corrupt_checkpoint_never_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            _make_pdf(pdf_path, pages=2)
            source_hash = source_document_sha256(pdf_path)
            out = root / "run"

            with _patched_two_crop_runtime(lambda _image, *, filename, **_kwargs: (_two_crop_fields(filename), [])):
                _attach_pdf_side_marker_evidence_to_study_diagrams(
                    pdf_path,
                    _diagrams(source_hash),
                    out,
                    dpi=96,
                    min_confidence=0.5,
                    quality_profile="default",
                    resume=False,
                )

            for mode in ("cold", "profile_mismatch", "corrupt"):
                calls: list[str] = []
                if mode == "corrupt":
                    checkpoint_path(out).write_text("not-json", encoding="utf-8")
                profile = "masterkindle" if mode == "profile_mismatch" else "default"

                def scan(_image, *, filename, **_kwargs):
                    calls.append(filename)
                    return _two_crop_fields(filename), []

                with _patched_two_crop_runtime(scan):
                    summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                        pdf_path,
                        _diagrams(source_hash),
                        out,
                        dpi=96,
                        min_confidence=0.5,
                        quality_profile=profile,
                        resume=mode != "cold",
                    )

                self.assertEqual(calls, ["first.png", "second.png"])
                self.assertFalse(summary["resume_used"])
                self.assertEqual(summary["reused_diagram_count"], 0)
                expected_reason = {
                    "cold": "resume_not_requested",
                    "profile_mismatch": "checkpoint_quality_profile_mismatch",
                    "corrupt": "checkpoint_corrupt",
                }[mode]
                self.assertEqual(summary["resume_reason_code"], expected_reason)

    def test_process_cli_forwards_resume_opt_in(self) -> None:
        captured = {}

        def fake_process(_input_path, **kwargs):
            captured.update(kwargs)
            return {"status": "AUTO_SUCCESS"}

        with (
            patch("chess_auto_flow.run_auto_chess_process", side_effect=fake_process),
            patch("sys.argv", ["kindlemaster.py", "process", "book.pdf", "--out", "out", "--resume"]),
        ):
            result = kindlemaster.main()

        self.assertEqual(result, 0)
        self.assertTrue(captured["resume"])

    def test_out_of_range_page_is_counted_as_computed_checkpoint_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            _make_pdf(pdf_path, pages=1)
            source_hash = source_document_sha256(pdf_path)
            diagram = _diagrams(source_hash)[1]

            with _patched_two_crop_runtime(
                lambda *_args, **_kwargs: self.fail("out-of-range page must not be rendered")
            ):
                summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                    pdf_path,
                    [diagram],
                    root / "run",
                    dpi=96,
                    min_confidence=0.5,
                    quality_profile="default",
                    resume=False,
                )

            self.assertEqual(summary["computed_diagram_count"], 1)
            self.assertEqual(summary["completed_diagram_count"], 1)
            self.assertEqual(summary["progress_percent"], 100.0)


def _identity() -> dict[str, object]:
    return build_checkpoint_identity(
        source_pdf_sha256="a" * 64,
        fingerprint_schema=DIAGRAM_FINGERPRINT_SCHEMA,
        dpi=96,
        quality_profile="default",
    )


def _make_pdf(path: Path, *, pages: int) -> None:
    document = fitz.open()
    for page_number in range(1, pages + 1):
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 20), f"Page {page_number}")
    document.save(path)
    document.close()


def _diagrams(source_hash: str) -> list[dict[str, object]]:
    return [
        {
            "diagram_id": "first",
            "diagram_fingerprint": "dfp_first",
            "source_document_sha256": source_hash,
            "page": 1,
            "pixel_bbox": [10.0, 10.0, 80.0, 80.0],
            "side_to_move": "unknown",
            "warnings": [],
        },
        {
            "diagram_id": "second",
            "diagram_fingerprint": "dfp_second",
            "source_document_sha256": source_hash,
            "page": 2,
            "pixel_bbox": [20.0, 20.0, 80.0, 80.0],
            "side_to_move": "unknown",
            "warnings": [],
        },
    ]


def _two_crop_fields(filename: str) -> dict[str, object]:
    return {
        "synthetic_two_crop_result": Path(filename).stem,
        "board_crop_quality": "pass",
        "board_crop_fail_reason": [],
        "marker_crop_quality": "fail",
        "marker_crop_fail_reason": ["marker_missing"],
        "manual_review_required": True,
        "manual_review_reason": "marker_missing",
        "two_crop_performance": {"total_seconds": 0.01},
    }


def _semantic_results(diagrams: list[dict[str, object]]) -> list[tuple[str, object]]:
    return [
        (str(row["diagram_fingerprint"]), row.get("synthetic_two_crop_result"))
        for row in diagrams
    ]


def _patched_two_crop_runtime(scan_side_effect):
    return _PatchStack(
        patch("chess_study_export._scan_chess_two_crop_review_artifacts", side_effect=scan_side_effect),
        patch(
            "chess_study_export._scan_chess_page_marker_pipeline",
            return_value={
                "candidates": [],
                "assignments": [],
                "files": [],
                "summary": {},
            },
        ),
        patch(
            "chess_study_export._scan_chess_apply_page_marker_assignment",
            side_effect=lambda payload, *_args, **_kwargs: payload,
        ),
        patch("chess_study_export._apply_scan_chess_two_crop_quality_gate", side_effect=lambda payload, *_args, **_kwargs: payload),
        patch("chess_study_export._apply_scan_chess_two_crop_side_marker_if_trusted", side_effect=lambda payload, *_args, **_kwargs: payload),
        patch(
            "chess_study_export._apply_study_side_marker_payload",
            side_effect=lambda diagram, payload: diagram.update(
                {"synthetic_two_crop_result": payload.get("synthetic_two_crop_result")}
            ),
        ),
        patch("chess_study_export._write_study_side_marker_artifact_files", return_value={"file_write_seconds": 0.0}),
        patch("chess_study_export._write_study_side_marker_report"),
        patch("chess_study_export._write_study_two_crop_quality_metrics"),
        patch("chess_study_export._write_study_side_marker_blocker_attribution"),
        patch("chess_study_export._write_study_side_to_move_diagnostic_report"),
        patch("chess_study_export._study_side_marker_summary", side_effect=lambda rows: {"diagram_count": len(rows)}),
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
