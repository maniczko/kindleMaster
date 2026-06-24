from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.audit_chess_pipeline_breakdown import audit_chess_pipeline_breakdown


class ChessPipelineAuditHarnessTests(unittest.TestCase):
    def test_empty_dataset_writes_required_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset(root)
            output = root / "reports"

            summary = audit_chess_pipeline_breakdown(manifest, output_dir=output)

            self.assertEqual(summary["dataset_validation_status"], "passed")
            self.assertEqual(summary["dataset_release_readiness"]["status"], "review_required")
            self.assertFalse(summary["dataset_release_readiness"]["accepted_for_release_proof"])
            self.assertEqual(summary["fen"]["diagram_detected_count"], 0)
            self.assertEqual(summary["negative"]["case_count"], 0)
            for relative in (
                "audit_summary.json",
                "audit_cases.jsonl",
                "audit_cases.csv",
                "top_fen_blockers.json",
                "top_pgn_blockers.json",
                "top_negative_blockers.json",
                "html/index.html",
            ):
                self.assertTrue((output / relative).exists(), relative)
            html = (output / "html" / "index.html").read_text(encoding="utf-8")
            self.assertIn("FEN funnel", html)
            self.assertIn("PGN funnel", html)
            self.assertIn("Negative samples", html)
            self.assertIn("Dataset release readiness", html)
            self.assertIn("review_required", html)
            self.assertIn("diagram_only", html)
            self.assertIn("audit_cases.jsonl", html)
            self.assertIn("top_fen_blockers.json", html)

    def test_diagram_only_pgn_is_infeasible_not_export_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset(root)
            (root / "labels" / "pgn_ground_truth.jsonl").write_text(
                json.dumps(
                    {
                        "id": "pgn-diagram-only",
                        "source_pdf": "book.pdf",
                        "page": 3,
                        "input_type": "diagram_only",
                        "pgn_feasible": False,
                        "pgn_feasibility_reason": "diagram_only_no_movetext",
                        "human_verified": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = audit_chess_pipeline_breakdown(manifest, output_dir=root / "reports")
            cases = [
                json.loads(line)
                for line in (root / "reports" / "audit_cases.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["pgn"]["case_count"], 1)
        self.assertEqual(summary["pgn"]["feasible_count"], 0)
        self.assertEqual(summary["pgn"]["exportable_count"], 0)
        self.assertEqual(cases[0]["top_pgn_blocker"], "pgn_infeasible:diagram_only_no_movetext")

    def test_feasible_pgn_reports_stage_funnel_separately_from_export_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset(root)
            (root / "labels" / "pgn_ground_truth.jsonl").write_text(
                json.dumps(
                    {
                        "id": "pgn-feasible-line",
                        "source_pdf": "book.pdf",
                        "page": 4,
                        "input_type": "full_game_text",
                        "pgn_feasible": True,
                        "pgn_feasibility_reason": "contains_movetext",
                        "expected_movetext": "1. e4 e5 2. Nf3 Nc6 *",
                        "human_verified": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = audit_chess_pipeline_breakdown(manifest, output_dir=root / "reports")
            cases = [
                json.loads(line)
                for line in (root / "reports" / "audit_cases.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["pgn"]["case_count"], 1)
        self.assertEqual(summary["pgn"]["feasible_count"], 1)
        self.assertEqual(summary["pgn"]["ocr_text_present_count"], 1)
        self.assertEqual(summary["pgn"]["candidate_blocks_found_count"], 1)
        self.assertEqual(summary["pgn"]["san_tokens_present_count"], 1)
        self.assertGreater(summary["pgn"]["san_token_count"], 0)
        self.assertEqual(summary["pgn"]["parse_clean_count"], 1)
        self.assertEqual(summary["pgn"]["replay_legal_count"], 1)
        self.assertEqual(summary["pgn"]["final_fen_present_count"], 1)
        self.assertFalse(cases[0]["exportable_pgn"])
        self.assertTrue(cases[0]["pgn_replay_legal"])

    def test_negative_samples_are_counted_separately_from_fen_and_pgn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset(root)
            crop = root / "crops" / "not_chess.png"
            crop.write_bytes(b"negative-crop")
            (root / "labels" / "negative_samples.jsonl").write_text(
                json.dumps(
                    {
                        "id": "negative-1",
                        "source_pdf": "book.pdf",
                        "page": 9,
                        "reason": "not_chess_diagram",
                        "crop_path": "crops/not_chess.png",
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-20",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            recognizer_result = _DummyRecognizerResult(
                {
                    "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                    "placement": "8/8/8/8/8/8/4K3/4k3",
                    "full_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                    "board_detected": True,
                    "warnings": [],
                }
            )
            with mock.patch("scripts.audit_chess_pipeline_breakdown.recognize_chess_position_from_image", return_value=recognizer_result):
                summary = audit_chess_pipeline_breakdown(manifest, output_dir=root / "reports")
            cases = [
                json.loads(line)
                for line in (root / "reports" / "audit_cases.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["dataset_validation_status"], "passed")
        self.assertEqual(summary["fen"]["case_count"], 0)
        self.assertEqual(summary["pgn"]["case_count"], 0)
        self.assertEqual(summary["negative"]["case_count"], 1)
        self.assertEqual(summary["negative"]["evaluable_count"], 1)
        self.assertEqual(summary["negative"]["false_positive_candidate_count"], 1)
        self.assertEqual(summary["negative"]["false_positive_runtime_count"], 1)
        self.assertEqual(cases[0]["case_type"], "negative")
        self.assertEqual(cases[0]["top_negative_blocker"], "negative_runtime_false_positive")

    def test_fen_crop_presence_is_not_treated_as_verified_crop_correctness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset(root)
            crop_a = root / "crops" / "fen_a.png"
            crop_b = root / "crops" / "fen_b.png"
            crop_a.write_bytes(b"fen-crop-a")
            crop_b.write_bytes(b"fen-crop-b")
            rows = [
                {
                    "id": "fen-without-crop-evidence",
                    "source_pdf": "book.pdf",
                    "page": 1,
                    "crop_path": "crops/fen_a.png",
                    "expected_placement": "8/8/8/8/8/8/4K3/4k3",
                    "human_verified": True,
                },
                {
                    "id": "fen-with-crop-evidence",
                    "source_pdf": "book.pdf",
                    "page": 2,
                    "crop_path": "crops/fen_b.png",
                    "crop_correct": True,
                    "expected_placement": "8/8/8/8/8/8/4K3/4k3",
                    "human_verified": True,
                },
            ]
            (root / "labels" / "fen_ground_truth.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            recognizer_result = _DummyRecognizerResult(
                {
                    "fen": "",
                    "placement": "",
                    "full_fen": "",
                    "board_detected": False,
                    "requires_review": True,
                    "warnings": [],
                }
            )
            diagnostics = {
                "grid_confidence": 0.5,
                "crop_problem_taxonomy": "clean_board",
                "warnings": [],
            }
            with (
                mock.patch("scripts.audit_chess_pipeline_breakdown.recognize_chess_position_from_image", return_value=recognizer_result),
                mock.patch("scripts.audit_chess_pipeline_breakdown.board_crop_grid_diagnostics_from_image", return_value=diagnostics),
                mock.patch("scripts.audit_chess_pipeline_breakdown.render_board_grid_overlay", return_value={"path": "overlay.png"}),
            ):
                summary = audit_chess_pipeline_breakdown(manifest, output_dir=root / "reports")
            cases = [
                json.loads(line)
                for line in (root / "reports" / "audit_cases.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["fen"]["case_count"], 2)
        self.assertEqual(summary["fen"]["crop_present_count"], 2)
        self.assertEqual(summary["fen"]["crop_correct_evidence_count"], 1)
        self.assertEqual(summary["fen"]["crop_correct_known_count"], 1)
        by_id = {case["id"]: case for case in cases}
        self.assertTrue(by_id["fen-without-crop-evidence"]["crop_present"])
        self.assertFalse(by_id["fen-without-crop-evidence"]["crop_correct_evidence_known"])
        self.assertFalse(by_id["fen-without-crop-evidence"]["crop_correct_known"])
        self.assertTrue(by_id["fen-with-crop-evidence"]["crop_correct_evidence_known"])
        self.assertTrue(by_id["fen-with-crop-evidence"]["crop_correct_known"])


def _write_dataset(root: Path) -> Path:
    (root / "labels").mkdir(parents=True)
    (root / "crops").mkdir()
    (root / "overlays").mkdir()
    for name in ("fen_ground_truth.jsonl", "pgn_ground_truth.jsonl", "negative_samples.jsonl"):
        (root / "labels" / name).write_text("", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "kindlemaster.chess_audit_dataset.v1",
                "fen_ground_truth": "labels/fen_ground_truth.jsonl",
                "pgn_ground_truth": "labels/pgn_ground_truth.jsonl",
                "negative_samples": "labels/negative_samples.jsonl",
                "crops_dir": "crops",
                "overlays_dir": "overlays",
            }
        ),
        encoding="utf-8",
    )
    return manifest


class _DummyRecognizerResult:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return dict(self._payload)


if __name__ == "__main__":
    unittest.main()
