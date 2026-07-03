from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_crop_qa_benchmark import evaluate_crop_qa_benchmark, write_crop_qa_diff_reports


BENCHMARK = Path("reference_inputs/chess_fen/qa/qa_crop_validation_rows.jsonl")
MANIFEST = Path("reference_inputs/chess_fen/qa/qa_crop_validation_manifest.json")
REQUIRED_FIELDS = {
    "diagram_id",
    "diagram_crop_status",
    "marker_crop_status",
    "visible_marker",
    "final_label",
    "issue_type",
    "QA_note",
    "recommended_fix",
}


class ChessCropQaBenchmarkTests(unittest.TestCase):
    def test_benchmark_has_200_rows_manifest_and_required_fields(self) -> None:
        rows = [json.loads(line) for line in BENCHMARK.read_text(encoding="utf-8").splitlines() if line.strip()]
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(len(rows), 200)
        self.assertEqual(manifest["schema"], "kindlemaster.chess_fen.crop_qa_benchmark.v1")
        self.assertEqual(manifest["record_count"], 200)
        self.assertTrue(REQUIRED_FIELDS.issubset(rows[0]))
        self.assertTrue(REQUIRED_FIELDS.issubset(set(manifest["fields"])))
        self.assertEqual(rows[0]["policy"], "manual_crop_qa_labels_evaluate_only_no_direct_fen_publication")

    def test_manifest_preserves_issue_acceptance_subsets(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        subsets = manifest["acceptance_subsets"]

        self.assertEqual(len(subsets["fragmentary_board_crops"]), 10)
        self.assertEqual(len(subsets["marker_system_conflicts"]), 9)
        self.assertEqual(len(subsets["marker_search_review_only"]), 4)
        self.assertIn("p010_d01", subsets["fragmentary_board_crops"])
        self.assertIn("p046_d01", subsets["marker_system_conflicts"])
        self.assertIn("p071_d06", subsets["marker_search_review_only"])

    def test_visible_marker_values_are_explicit_not_placeholder(self) -> None:
        rows = [json.loads(line) for line in BENCHMARK.read_text(encoding="utf-8").splitlines() if line.strip()]
        visible_counts = {value: sum(1 for row in rows if row["visible_marker"] == value) for value in {"\u25b3", "\u25bc"}}

        self.assertEqual(visible_counts["\u25b3"], 120)
        self.assertEqual(visible_counts["\u25bc"], 45)
        self.assertFalse(
            [
                row["diagram_id"]
                for row in rows
                if row.get("source_manual_visible_marker") in {"outline_triangle", "filled_triangle"}
                and row.get("visible_marker") == "?"
            ]
        )

    def test_fragmentary_board_crop_subset_cannot_pass_quality(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        actual = [
            {"diagram_id": diagram_id, "board_crop_quality": "pass", "side_marker_status": "marker_missing"}
            for diagram_id in manifest["acceptance_subsets"]["fragmentary_board_crops"]
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            actual_path = Path(temp_dir) / "actual.json"
            actual_path.write_text(json.dumps({"items": actual}, ensure_ascii=False), encoding="utf-8")
            report = evaluate_crop_qa_benchmark(BENCHMARK, actual_path=actual_path, manifest_path=MANIFEST)

        reasons = {row["reason"] for row in report["regressions"]}
        self.assertEqual(report["summary"]["regression_count"], 10)
        self.assertEqual(reasons, {"fragmentary_board_crop_passed"})

    def test_marker_conflict_subset_cannot_promote_visible_black_to_white(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        actual = [
            {
                "diagram_id": diagram_id,
                "board_crop_quality": "pass",
                "marker_crop_quality": "pass",
                "side_marker_status": "trusted_marker",
                "side_to_move": "w",
            }
            for diagram_id in manifest["acceptance_subsets"]["marker_system_conflicts"]
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            actual_path = Path(temp_dir) / "actual.jsonl"
            actual_path.write_text("\n".join(json.dumps(row) for row in actual) + "\n", encoding="utf-8")
            report = evaluate_crop_qa_benchmark(BENCHMARK, actual_path=actual_path, manifest_path=MANIFEST)

        self.assertEqual(report["summary"]["regression_count"], 9)
        self.assertEqual({row["reason"] for row in report["regressions"]}, {"visible_black_marker_promoted_to_white"})

    def test_review_only_marker_statuses_cannot_become_trusted_marker(self) -> None:
        rows = [json.loads(line) for line in BENCHMARK.read_text(encoding="utf-8").splitlines() if line.strip()]
        unsafe = [row for row in rows if row["marker_crop_status"] in {"none", "unclear", "multiple", "bad_crop", "cropped_marker"}][:5]
        self.assertTrue(unsafe)
        actual = [
            {
                "diagram_id": row["diagram_id"],
                "board_crop_quality": "pass",
                "marker_crop_quality": "pass",
                "side_marker_status": "trusted_marker",
                "side_to_move": row.get("final_label") or "w",
            }
            for row in unsafe
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            actual_path = Path(temp_dir) / "actual.json"
            out_path = Path(temp_dir) / "crop_qa_regression_diff.json"
            actual_path.write_text(json.dumps(actual, ensure_ascii=False), encoding="utf-8")
            report = evaluate_crop_qa_benchmark(BENCHMARK, actual_path=actual_path, manifest_path=MANIFEST)
            json_path, md_path = write_crop_qa_diff_reports(report, out_path)
            written = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")

        self.assertGreaterEqual(report["summary"]["regression_count"], len(unsafe))
        self.assertIn("unsafe_marker_status_promoted_to_trusted", {row["reason"] for row in report["regressions"]})
        self.assertEqual(written["schema"], "kindlemaster.chess_fen.crop_qa_regression_diff.v1")
        self.assertIn("# Chess Crop QA Regression Diff", markdown)

    def test_review_only_safe_actual_rows_are_not_regressions(self) -> None:
        actual = [
            {
                "diagram_id": "p046_d01",
                "board_crop_quality": "pass",
                "marker_crop_quality": "fail",
                "side_marker_status": "marker_conflict",
                "side_to_move": "unknown",
            },
            {
                "diagram_id": "p071_d06",
                "board_crop_quality": "pass",
                "marker_crop_quality": "fail",
                "side_marker_status": "ambiguous_marker",
                "side_to_move": "unknown",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            actual_path = Path(temp_dir) / "actual.json"
            actual_path.write_text(json.dumps({"items": actual}, ensure_ascii=False), encoding="utf-8")
            report = evaluate_crop_qa_benchmark(BENCHMARK, actual_path=actual_path, manifest_path=MANIFEST)

        self.assertEqual(report["summary"]["regression_count"], 0)
        self.assertEqual(report["summary"]["manual_review_required_count"], 2)


if __name__ == "__main__":
    unittest.main()
