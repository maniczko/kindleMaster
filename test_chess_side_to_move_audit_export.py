from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from chess_side_to_move_audit_export import (
    discover_latest_audit_job,
    export_side_to_move_audit,
    extract_key_metrics,
    extract_top_blockers,
    format_audit_export_console,
)


class ChessSideToMoveAuditExportTests(unittest.TestCase):
    def test_latest_job_discovery_uses_newest_allowlisted_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = (
                root
                / "job-older"
                / "reports"
                / "chess_fen"
                / "why_side_to_move_not_trusted.json"
            )
            newer = (
                root
                / "nested"
                / "job-newer"
                / "reports"
                / "chess_fen"
                / "side_to_move_coverage_dashboard.json"
            )
            self._write_json(older, {"summary": {"diagram_count": 1}})
            self._write_json(newer, {"summary": {"diagram_count": 2}})
            ignored = (
                root
                / "node_modules"
                / "fake-job"
                / "reports"
                / "chess_fen"
                / "why_side_to_move_not_trusted.json"
            )
            self._write_json(ignored, {"summary": {"diagram_count": 999}})
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            os.utime(ignored, (300, 300))

            result = discover_latest_audit_job([root, root])

        self.assertEqual(Path(result["selected_job_output"]).name, "job-newer")
        self.assertEqual(len(result["checked_search_roots"]), 1)
        self.assertEqual(len(result["candidate_job_outputs"]), 2)

    def test_export_creates_safe_zip_and_excludes_pdf_env_and_crops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            why = job / "reports" / "chess_fen" / "why_side_to_move_not_trusted.json"
            why_md = why.with_suffix(".md")
            why_html = why.with_suffix(".html")
            self._write_json(why, {"summary": {"diagram_count": 3}})
            why_md.write_text("# audit\n", encoding="utf-8")
            why_html.write_text("<h1>audit</h1>\n", encoding="utf-8")
            (job / "source.pdf").write_bytes(b"%PDF")
            (job / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            crop = job / "review" / "chess_fen" / "two_crop" / "marker.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"png")
            archive_path = root / "bundle.zip"

            payload = export_side_to_move_audit(
                job_output=job,
                out_path=archive_path,
            )

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        self.assertEqual(payload["status"], "created")
        self.assertEqual(
            names,
            {
                "reports/chess_fen/why_side_to_move_not_trusted.json",
                "reports/chess_fen/why_side_to_move_not_trusted.md",
                "reports/chess_fen/why_side_to_move_not_trusted.html",
            },
        )
        self.assertNotIn("source.pdf", names)
        self.assertNotIn(".env", names)
        self.assertFalse(any(name.endswith(".png") for name in names))

    def test_no_include_html_omits_allowlisted_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            report = job / "reports" / "chess_fen" / "why_side_to_move_not_trusted.json"
            self._write_json(report, {"summary": {"diagram_count": 1}})
            report.with_suffix(".html").write_text("<h1>audit</h1>\n", encoding="utf-8")
            archive_path = root / "bundle.zip"

            payload = export_side_to_move_audit(
                job_output=job,
                out_path=archive_path,
                include_html=False,
            )
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())

        self.assertEqual(payload["status"], "created")
        self.assertEqual(names, {"reports/chess_fen/why_side_to_move_not_trusted.json"})

    def test_metrics_and_top_blockers_merge_diagnostic_and_coverage_reports(
        self,
    ) -> None:
        reports = {
            "reports/chess_fen/why_side_to_move_not_trusted.json": {
                "summary": {
                    "diagram_count": 12,
                    "side_unknown_count": 2,
                    "marker_search_zone_coverage_rate": 1.0,
                    "marker_bbox_detection_rate": 0.9167,
                    "marker_crop_generation_rate": 0.8333,
                    "marker_crop_quality_pass_rate": 0.75,
                    "trusted_marker_rate": 0.5,
                    "side_to_move_coverage_rate": 0.8333,
                    "by_primary_blocker": {
                        "marker_bbox_not_found": 2,
                        "marker_classifier_ambiguous": 3,
                        "no_blocker_trusted": 6,
                    },
                }
            },
            "reports/chess_fen/side_to_move_coverage_dashboard.json": {
                "summary": {
                    "diagram_count": 12,
                    "unknown_count": 2,
                    "trusted_marker_rate": 0.5833,
                    "side_to_move_coverage_rate": 0.8333,
                    "trusted_side_to_move_rate": 0.6667,
                    "full_fen_safe_acceptance_rate": 0.4167,
                }
            },
        }

        metrics = extract_key_metrics(reports)
        blockers = extract_top_blockers(reports)

        self.assertEqual(metrics["diagram_count"], 12)
        self.assertEqual(metrics["side_unknown_count"], 2)
        self.assertEqual(metrics["marker_search_zone_coverage_rate"], 1.0)
        self.assertEqual(metrics["trusted_marker_rate"], 0.5833)
        self.assertEqual(metrics["trusted_side_to_move_rate"], 0.6667)
        self.assertEqual(metrics["full_fen_safe_acceptance_rate"], 0.4167)
        self.assertEqual(
            blockers,
            [
                {"code": "marker_classifier_ambiguous", "count": 3},
                {"code": "marker_bbox_not_found", "count": 2},
            ],
        )

    def test_missing_reports_return_clear_diagnostic_and_checked_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job = Path(temp_dir) / "empty-job"
            job.mkdir()

            payload = export_side_to_move_audit(job_output=job)
            console = format_audit_export_console(payload)

        self.assertEqual(payload["status"], "no_diagnostics")
        self.assertEqual(payload["zip_path"], "")
        self.assertIn("contains none of the allowlisted", payload["message"])
        self.assertIn("CHECKED DIRECTORIES:", console)
        self.assertIn(str(job), console)
        self.assertIn("- diagram_count: n/a", console)

    def test_latest_missing_reports_lists_every_search_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing"
            empty = root / "empty"
            empty.mkdir()

            payload = export_side_to_move_audit(
                latest=True,
                search_roots=[missing, empty],
            )

        self.assertEqual(payload["status"], "job_not_found")
        self.assertEqual(
            payload["checked_search_roots"],
            [str(missing.resolve()), str(empty.resolve())],
        )

    def test_json_summary_and_cli_output_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = root / "job"
            report = job / "reports" / "chess_fen" / "why_side_to_move_not_trusted.json"
            self._write_json(
                report, {"summary": {"diagram_count": 4, "side_unknown_count": 1}}
            )
            archive_path = root / "bundle.zip"
            summary_path = root / "bundle.summary.json"
            stdout = io.StringIO()
            argv = [
                "kindlemaster.py",
                "chess",
                "export-side-to-move-audit",
                "--job-output",
                str(job),
                "--out",
                str(archive_path),
                "--json-summary",
                str(summary_path),
            ]

            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                exit_code = kindlemaster.main()
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            archive_exists = archive_path.is_file()

        self.assertEqual(exit_code, 0)
        self.assertTrue(archive_exists)
        self.assertEqual(summary["status"], "created")
        self.assertEqual(summary["metrics"]["diagram_count"], 4)
        self.assertIn("STATUS:", stdout.getvalue())
        self.assertIn("KEY METRICS:", stdout.getvalue())
        self.assertIn("- diagram_count: 4", stdout.getvalue())

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main()
