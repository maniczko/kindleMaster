from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from scripts.generate_project_status import generate_project_status


class ProjectStatusTests(unittest.TestCase):
    def test_generate_project_status_uses_corpus_gate_and_latest_completed_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            (repo_root / ".github" / "workflows").mkdir(parents=True)
            (repo_root / "docs").mkdir(parents=True)
            (reports_root / "corpus").mkdir(parents=True)
            workflow_dir = reports_root / "workflows" / "20260422T100000Z-example"
            workflow_dir.mkdir(parents=True)

            (repo_root / ".github" / "workflows" / "ready-enforcement.yml").write_text("name: READY Enforcement\n", encoding="utf-8")
            (repo_root / "docs" / "github-ready-enforcement.md").write_text("# GitHub READY Enforcement\n", encoding="utf-8")
            (reports_root / "corpus" / "corpus_gate.json").write_text(
                json.dumps(
                    {
                        "overall_status": "passed_with_warnings",
                        "proof_profile": "standard",
                        "smoke": {"summary": {"overall_status": "passed"}},
                        "premium_corpus": {
                            "overall_status": "passed_with_warnings",
                            "overall": {
                                "converted_case_count": 2,
                                "analysis_only_case_count": 1,
                                "grade_counts": {"pass_with_review": 1},
                                "blocker_counts": {},
                                "warning_counts": {"heading_manual_review": 1},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (workflow_dir / "verification.json").write_text(
                json.dumps({"run_id": "run-1", "status": "failed", "change_area": "semantic"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (workflow_dir / "before_after.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "regression_pack_status": "passed",
                        "smoke_status": "passed",
                        "remaining_risks": ["gate B failed"],
                        "unresolved_warnings": ["gate B failed"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

            self.assertEqual(payload["overall_status"], "passed_with_warnings")
            self.assertEqual(payload["corpus"]["status"], "passed_with_warnings")
            self.assertEqual(payload["workflow"]["status"], "failed")
            self.assertTrue((reports_root / "project_status.json").exists())
            self.assertTrue((reports_root / "project_status.md").exists())

    def test_generate_project_status_markdown_keeps_core_evidence_paths_from_rich_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            (repo_root / ".github" / "workflows").mkdir(parents=True)
            (repo_root / "docs").mkdir(parents=True)
            (reports_root / "corpus").mkdir(parents=True)
            workflow_dir = reports_root / "workflows" / "20260422T120000Z-derived-status"
            workflow_dir.mkdir(parents=True)

            ready_workflow_path = repo_root / ".github" / "workflows" / "ready-enforcement.yml"
            ready_doc_path = repo_root / "docs" / "github-ready-enforcement.md"
            corpus_gate_path = reports_root / "corpus" / "corpus_gate.json"

            ready_workflow_path.write_text("name: READY Enforcement\n", encoding="utf-8")
            ready_doc_path.write_text("# GitHub READY Enforcement\n", encoding="utf-8")
            corpus_gate_path.write_text(
                json.dumps(
                    {
                        "overall_status": "passed",
                        "proof_profile": "standard",
                        "smoke": {"summary": {"overall_status": "passed"}},
                        "premium_corpus": {
                            "overall_status": "passed",
                            "overall": {
                                "converted_case_count": 2,
                                "analysis_only_case_count": 0,
                                "grade_counts": {"pass": 2},
                                "blocker_counts": {},
                                "warning_counts": {},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (workflow_dir / "verification.json").write_text(
                json.dumps({"run_id": "run-2", "status": "passed", "change_area": "corpus"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (workflow_dir / "before_after.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "regression_pack_status": "passed",
                        "smoke_status": "passed",
                        "remaining_risks": [],
                        "unresolved_warnings": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

            markdown = (reports_root / "project_status.md").read_text(encoding="utf-8")

        self.assertIn("# KindleMaster Project Status", markdown)
        self.assertIn("Overall status: `passed`", markdown)
        self.assertIn(f"Corpus gate JSON: `{corpus_gate_path}`", markdown)
        self.assertIn(f"Latest workflow reports dir: `{workflow_dir}`", markdown)
        self.assertIn(f"READY workflow: `{ready_workflow_path}`", markdown)

    def test_generate_project_status_warns_when_governance_artifacts_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            (reports_root / "corpus").mkdir(parents=True)

            (reports_root / "corpus" / "corpus_gate.json").write_text(
                json.dumps(
                    {
                        "overall_status": "passed",
                        "proof_profile": "full",
                        "smoke": {"summary": {"overall_status": "passed"}},
                        "premium_corpus": {
                            "overall_status": "passed",
                            "overall": {
                                "converted_case_count": 3,
                                "analysis_only_case_count": 0,
                                "grade_counts": {"pass": 3},
                                "blocker_counts": {},
                                "warning_counts": {},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

            self.assertEqual(payload["overall_status"], "passed_with_warnings")
            self.assertFalse(payload["governance"]["ready_workflow_present"])
            self.assertFalse(payload["governance"]["ready_doc_present"])
            self.assertIn("GitHub READY workflow evidence is missing.", payload["warnings"])
            self.assertIn("GitHub READY enforcement documentation is missing.", payload["warnings"])

    def test_generate_project_status_markdown_keeps_core_evidence_paths_minimal_pass_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            (repo_root / ".github" / "workflows").mkdir(parents=True)
            (repo_root / "docs").mkdir(parents=True)
            (reports_root / "corpus").mkdir(parents=True)
            workflow_dir = reports_root / "workflows" / "20260422T100000Z-example"
            workflow_dir.mkdir(parents=True)

            ready_workflow = repo_root / ".github" / "workflows" / "ready-enforcement.yml"
            ready_doc = repo_root / "docs" / "github-ready-enforcement.md"
            ready_workflow.write_text("name: READY Enforcement\n", encoding="utf-8")
            ready_doc.write_text("# GitHub READY Enforcement\n", encoding="utf-8")
            (reports_root / "corpus" / "corpus_gate.json").write_text(
                json.dumps(
                    {
                        "overall_status": "passed",
                        "proof_profile": "full",
                        "smoke": {"summary": {"overall_status": "passed"}},
                        "premium_corpus": {
                            "overall_status": "passed",
                            "overall": {
                                "converted_case_count": 3,
                                "analysis_only_case_count": 0,
                                "grade_counts": {"pass": 3},
                                "blocker_counts": {},
                                "warning_counts": {},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (workflow_dir / "verification.json").write_text(
                json.dumps({"run_id": "run-1", "status": "passed", "change_area": "semantic"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (workflow_dir / "before_after.json").write_text(
                json.dumps({"status": "passed", "regression_pack_status": "passed", "smoke_status": "passed"}, ensure_ascii=False),
                encoding="utf-8",
            )

            generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

            markdown = (reports_root / "project_status.md").read_text(encoding="utf-8")

            self.assertIn("# KindleMaster Project Status", markdown)
            self.assertIn("Overall status: `passed`", markdown)
            self.assertIn(str(reports_root / "corpus" / "corpus_gate.json"), markdown)
            self.assertIn(str(workflow_dir), markdown)
            self.assertIn(str(ready_workflow), markdown)

    def test_kindlemaster_status_command_routes_to_generator(self) -> None:
        payload = {"overall_status": "passed", "corpus": {"status": "passed"}, "workflow": {"status": "passed"}, "governance": {"ready_workflow_present": True}}
        with patch("scripts.generate_project_status.generate_project_status", return_value=payload) as generator_mock, patch.object(
            kindlemaster,
            "_print_json",
        ) as print_mock, patch(
            "sys.argv",
            [
                "kindlemaster.py",
                "status",
                "--repo-root",
                ".",
                "--reports-root",
                "reports",
                "--output-json",
                "reports/project_status.json",
                "--output-md",
                "reports/project_status.md",
            ],
        ):
            exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        generator_mock.assert_called_once_with(
            repo_root=".",
            reports_root="reports",
            output_json="reports/project_status.json",
            output_md="reports/project_status.md",
        )
        print_mock.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
