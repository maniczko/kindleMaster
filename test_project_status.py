from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from scripts.generate_project_status import generate_project_status


class ProjectStatusTests(unittest.TestCase):
    def test_governance_docs_for_linear_zones_exist_and_cross_link(self) -> None:
        required_docs = {
            "docs/conversion-pipeline.md": ["VAT-173", "python kindlemaster.py test --suite corpus"],
            "docs/source-of-truth-matrix.md": ["VAT-131", "VAT-132", "reports/project_status.json"],
            "docs/independent-audit-mode.md": ["VAT-134", "python kindlemaster.py audit"],
            "docs/local-bootstrap-toolchain.md": ["VAT-126", "python kindlemaster.py doctor"],
            "docs/linear-issue-template.md": ["VAT-174", "Affected conversion-quality area"],
            "docs/premium-epub-release-checklist.md": ["VAT-176", "Premium EPUB release checklist"],
            "docs/product-scope.md": ["VAT-215", "VAT-216", "docs/v2-reader-workflow-roadmap.md"],
            "docs/v2-reader-workflow-roadmap.md": ["VAT-215", "VAT-216", "VAT-204", "release_ready", "Obsidian", "Readwise"],
        }

        for relative_path, markers in required_docs.items():
            with self.subTest(path=relative_path):
                content = Path(relative_path).read_text(encoding="utf-8")
                self.assertIn("# ", content)
                for marker in markers:
                    self.assertIn(marker, content)

        readme_text = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("docs/conversion-pipeline.md", readme_text)
        self.assertIn("docs/source-of-truth-matrix.md", readme_text)
        self.assertIn("docs/premium-epub-release-checklist.md", readme_text)
        self.assertIn("docs/linear-issue-template.md", readme_text)
        self.assertIn("docs/v2-reader-workflow-roadmap.md", readme_text)

    def test_governance_dashboard_doc_documents_active_session_overrides(self) -> None:
        content = Path("docs/governance-dashboard.md").read_text(encoding="utf-8")

        self.assertIn("VAT-206", content)
        self.assertIn("python kindlemaster.py status", content)
        self.assertIn("Evidence Freshness", content)
        self.assertIn("Workflow Completeness", content)
        self.assertIn("Active Session Overrides", content)
        self.assertIn(".codex/config.toml", content)
        self.assertIn(".codex/README.md", content)
        self.assertIn("current session policy wins", content)
        self.assertIn("repo-local defaults", content)

    def _write_vat206_governance_sources(self, repo_root: Path) -> None:
        (repo_root / ".codex").mkdir(parents=True, exist_ok=True)
        (repo_root / "docs").mkdir(parents=True, exist_ok=True)
        (repo_root / "kindlemaster.py").write_text(
            """
subparsers.add_parser("bootstrap")
subparsers.add_parser("doctor")
subparsers.add_parser("prepare-reference-inputs")
subparsers.add_parser("serve")
subparsers.add_parser("convert")
subparsers.add_parser("validate")
subparsers.add_parser("smoke")
subparsers.add_parser("corpus")
subparsers.add_parser("status")
test_parser = subparsers.add_parser("test")
test_parser.add_argument("--suite", choices=("quick", "release", "full", "browser", "runtime", "corpus"), default="quick")
subparsers.add_parser("audit")
workflow_parser = subparsers.add_parser("workflow")
workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command")
workflow_subparsers.add_parser("baseline")
workflow_subparsers.add_parser("verify")
""",
            encoding="utf-8",
        )
        command_mirror = """
python kindlemaster.py bootstrap
python kindlemaster.py doctor
python kindlemaster.py prepare-reference-inputs
python kindlemaster.py serve
python kindlemaster.py convert input.pdf --output output.epub
python kindlemaster.py validate output.epub
python kindlemaster.py smoke --mode quick
python kindlemaster.py corpus
python kindlemaster.py status
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite corpus
python kindlemaster.py test --suite release
python kindlemaster.py test --suite browser
python kindlemaster.py test --suite runtime
python kindlemaster.py audit output.epub
python kindlemaster.py workflow baseline input.pdf --change-area corpus
reports/project_status.json
reports/project_status.md
"""
        (repo_root / "README.md").write_text(f"# README\n{command_mirror}", encoding="utf-8")
        (repo_root / "AGENTS.md").write_text(
            (
                "# AGENTS\n"
                "- `bootstrap`\n- `doctor`\n- `prepare-reference-inputs`\n- `serve`\n- `convert`\n"
                "- `validate`\n- `smoke`\n- `corpus`\n- `status`\n- `test`\n- `audit`\n- `workflow`\n"
                "python kindlemaster.py status\nreports/project_status.json\nreports/project_status.md\n"
            ),
            encoding="utf-8",
        )
        (repo_root / ".codex" / "config.toml").write_text(
            f"# generated output/ and reports/ artifacts are derived evidence\n{command_mirror}",
            encoding="utf-8",
        )
        (repo_root / ".codex" / "README.md").write_text(
            (
                "# KindleMaster Codex Project Config\n"
                "- Generated files under `reports/` and `output/` are derived runtime artifacts, never governance authority.\n"
                f"{command_mirror}"
            ),
            encoding="utf-8",
        )
        (repo_root / "docs" / "toolchain-matrix.md").write_text(
            "\n".join(
                [
                    "# Toolchain Matrix",
                    "python kindlemaster.py test --suite quick",
                    "python kindlemaster.py test --suite corpus",
                    "python kindlemaster.py test --suite release",
                    "python kindlemaster.py test --suite browser",
                    "python kindlemaster.py test --suite runtime",
                ]
            ),
            encoding="utf-8",
        )
        (repo_root / "docs" / "governance-dashboard.md").write_text(
            (
                "# VAT-206 Governance Dashboard\n"
                "## Active Session Overrides\n"
                ".codex/config.toml stores repo-local defaults. The current session policy wins for this run."
            ),
            encoding="utf-8",
        )

    def _write_complete_workflow(
        self,
        workflow_dir: Path,
        *,
        run_id: str,
        status: str = "passed",
        change_area: str = "corpus",
        input_type: str = "pdf",
        regression_pack_status: str = "passed",
        smoke_status: str = "passed",
        report_complete: bool = True,
        remaining_risks: list[str] | None = None,
        unresolved_warnings: list[str] | None = None,
    ) -> None:
        workflow_dir.mkdir(parents=True, exist_ok=True)
        baseline = {
            "run_id": run_id,
            "mode": "baseline",
            "input_path": str(workflow_dir / "input.pdf"),
            "input_type": input_type,
            "change_area": change_area,
            "snapshot": {"status": status},
        }
        verification = {
            "run_id": run_id,
            "mode": "verify",
            "status": status,
            "change_area": change_area,
            "input_type": input_type,
            "baseline_status": status,
            "verification_snapshot": {"status": status, "symptoms": remaining_risks or []},
        }
        before_after = {
            "run_id": run_id,
            "status": status,
            "report_complete": report_complete,
            "regression_pack_status": regression_pack_status,
            "smoke_status": smoke_status,
            "remaining_risks": remaining_risks or [],
            "unresolved_warnings": unresolved_warnings or [],
        }
        regression_pack = {"status": regression_pack_status, "tests": []}
        smoke_pack = {"status": smoke_status, "executed": []}

        (workflow_dir / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
        (workflow_dir / "baseline.md").write_text("# Baseline\n", encoding="utf-8")
        (workflow_dir / "isolation.json").write_text(
            json.dumps({"run_id": run_id, "change_area": change_area, "input_type": input_type}, ensure_ascii=False),
            encoding="utf-8",
        )
        (workflow_dir / "verification.json").write_text(json.dumps(verification, ensure_ascii=False), encoding="utf-8")
        (workflow_dir / "verification.md").write_text("# Verification\n", encoding="utf-8")
        (workflow_dir / "before_after.json").write_text(json.dumps(before_after, ensure_ascii=False), encoding="utf-8")
        (workflow_dir / "before_after.md").write_text("# Before After\n", encoding="utf-8")
        (workflow_dir / "regression_pack.json").write_text(json.dumps(regression_pack, ensure_ascii=False), encoding="utf-8")
        (workflow_dir / "regression_pack.md").write_text("# Regression\n", encoding="utf-8")
        (workflow_dir / "smoke_pack.json").write_text(json.dumps(smoke_pack, ensure_ascii=False), encoding="utf-8")
        (workflow_dir / "smoke_pack.md").write_text("# Smoke\n", encoding="utf-8")

    def _write_passing_status_inputs(self, repo_root: Path, reports_root: Path) -> None:
        (repo_root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (repo_root / "docs").mkdir(parents=True, exist_ok=True)
        (reports_root / "corpus").mkdir(parents=True, exist_ok=True)
        workflow_dir = reports_root / "workflows" / "20260422T120000Z-vat206"
        (repo_root / ".github" / "workflows" / "ready-enforcement.yml").write_text("name: READY Enforcement\n", encoding="utf-8")
        (repo_root / "docs" / "github-ready-enforcement.md").write_text("# GitHub READY Enforcement\n", encoding="utf-8")
        (reports_root / "corpus" / "corpus_gate.json").write_text(
            json.dumps(
                {
                    "overall_status": "passed",
                    "proof_profile": "standard",
                    "smoke": {"summary": {"overall_status": "passed"}},
                    "premium_corpus": {
                        "overall_status": "passed",
                        "overall": {
                            "converted_case_count": 1,
                            "analysis_only_case_count": 0,
                            "grade_counts": {"pass": 1},
                            "blocker_counts": {},
                            "warning_counts": {},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._write_complete_workflow(workflow_dir, run_id="run-vat206", status="passed", change_area="corpus")

    def test_generate_project_status_builds_vat206_dashboard_and_drift_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            self._write_vat206_governance_sources(repo_root)
            self._write_passing_status_inputs(repo_root, reports_root)
            governance_root = reports_root / "governance"
            governance_root.mkdir(parents=True, exist_ok=True)
            (governance_root / "doctor.json").write_text(
                json.dumps(
                    {
                        "verification_surfaces": {
                            "quick": {"status": "supported"},
                            "corpus": {"status": "supported"},
                            "release": {"status": "supported"},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (governance_root / "quick.json").write_text(json.dumps({"suite": "quick", "status": "passed"}), encoding="utf-8")
            (governance_root / "release.json").write_text(
                json.dumps({"suite": "release", "status": "passed_with_warnings"}),
                encoding="utf-8",
            )

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )
            markdown = (reports_root / "project_status.md").read_text(encoding="utf-8")

        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["dashboard"]["evidence"]["doctor"]["status"], "passed")
        self.assertEqual(payload["dashboard"]["evidence"]["quick"]["status"], "passed")
        self.assertEqual(payload["dashboard"]["evidence"]["corpus"]["status"], "passed")
        self.assertEqual(payload["dashboard"]["evidence"]["release"]["status"], "passed_with_warnings")
        self.assertEqual(payload["governance"]["drift_status"], "passed")
        self.assertTrue(payload["governance"]["session_override"]["documented"])
        command_check = next(
            check for check in payload["governance"]["drift_checks"] if check["id"] == "first_class_command_mirrors"
        )
        self.assertIn(".codex/README.md", command_check["mirror_sources"])
        self.assertIn("VAT-206 Governance Dashboard", markdown)
        self.assertIn("Active Session Overrides", markdown)

    def test_generate_project_status_warns_for_stale_evidence_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            self._write_vat206_governance_sources(repo_root)
            self._write_passing_status_inputs(repo_root, reports_root)
            governance_root = reports_root / "governance"
            governance_root.mkdir(parents=True, exist_ok=True)
            quick_path = governance_root / "quick.json"
            quick_path.write_text(json.dumps({"suite": "quick", "status": "passed"}), encoding="utf-8")
            os.utime(quick_path, (0, 0))

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

        self.assertEqual(payload["dashboard"]["evidence"]["quick"]["freshness_status"], "stale")
        self.assertIn("Evidence lane `quick` is stale", "\n".join(payload["warnings"]))

    def test_generate_project_status_summarizes_workflow_completeness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            self._write_vat206_governance_sources(repo_root)
            self._write_passing_status_inputs(repo_root, reports_root)
            complete_run_id = "20260422T100000Z-complete"
            baseline_only_run_id = "20260423T100000Z-baseline-only"
            self._write_complete_workflow(
                reports_root / "workflows" / complete_run_id,
                run_id=complete_run_id,
                status="passed",
                change_area="semantic",
            )
            baseline_only_dir = reports_root / "workflows" / baseline_only_run_id
            baseline_only_dir.mkdir(parents=True, exist_ok=True)
            (baseline_only_dir / "baseline.json").write_text(
                json.dumps({"run_id": baseline_only_run_id, "change_area": "semantic", "input_type": "pdf", "snapshot": {"status": "passed"}}),
                encoding="utf-8",
            )
            (baseline_only_dir / "baseline.md").write_text("# Baseline\n", encoding="utf-8")
            (baseline_only_dir / "isolation.json").write_text(json.dumps({"run_id": baseline_only_run_id}), encoding="utf-8")

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )
            markdown = (reports_root / "project_status.md").read_text(encoding="utf-8")

        completeness = payload["workflow"]["completeness"]
        self.assertEqual(completeness["complete_count"], 2)
        self.assertEqual(completeness["incomplete_count"], 1)
        self.assertEqual(completeness["baseline_only_count"], 1)
        self.assertEqual(completeness["latest_incomplete"]["run_id"], baseline_only_run_id)
        self.assertIn("Latest incomplete workflow", "\n".join(payload["warnings"]))
        self.assertIn("Workflow Completeness", markdown)

    def test_generate_project_status_does_not_warn_for_older_incomplete_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            self._write_vat206_governance_sources(repo_root)
            self._write_passing_status_inputs(repo_root, reports_root)
            baseline_only_run_id = "20260421T100000Z-baseline-only"
            baseline_only_dir = reports_root / "workflows" / baseline_only_run_id
            baseline_only_dir.mkdir(parents=True, exist_ok=True)
            (baseline_only_dir / "baseline.json").write_text(
                json.dumps({"run_id": baseline_only_run_id, "change_area": "semantic", "input_type": "pdf", "snapshot": {"status": "passed"}}),
                encoding="utf-8",
            )
            (baseline_only_dir / "baseline.md").write_text("# Baseline\n", encoding="utf-8")
            for item in [baseline_only_dir, *baseline_only_dir.rglob("*")]:
                os.utime(item, (0, 0))

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

        self.assertEqual(payload["workflow"]["completeness"]["latest_incomplete"]["run_id"], baseline_only_run_id)
        self.assertNotIn("Latest incomplete workflow", "\n".join(payload["warnings"]))

    def test_generate_project_status_flags_governance_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            self._write_vat206_governance_sources(repo_root)
            self._write_passing_status_inputs(repo_root, reports_root)
            (repo_root / "docs" / "toolchain-matrix.md").write_text(
                "# Toolchain Matrix\npython kindlemaster.py test --suite quick\n",
                encoding="utf-8",
            )

            payload = generate_project_status(
                repo_root=repo_root,
                reports_root=reports_root,
                output_json=reports_root / "project_status.json",
                output_md=reports_root / "project_status.md",
            )

        self.assertEqual(payload["overall_status"], "passed_with_warnings")
        self.assertEqual(payload["governance"]["drift_status"], "failed")
        self.assertIn("Governance drift checks detected mismatched command or policy mirrors.", payload["warnings"])
        toolchain_check = next(
            check for check in payload["governance"]["drift_checks"] if check["id"] == "toolchain_suite_command_mirrors"
        )
        self.assertIn("python kindlemaster.py test --suite release", toolchain_check["missing_by_source"]["docs/toolchain-matrix.md"])

    def test_generate_project_status_uses_corpus_gate_and_latest_completed_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            reports_root = repo_root / "reports"
            (repo_root / ".github" / "workflows").mkdir(parents=True)
            (repo_root / "docs").mkdir(parents=True)
            (reports_root / "corpus").mkdir(parents=True)
            workflow_dir = reports_root / "workflows" / "20260422T100000Z-example"

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
            self._write_complete_workflow(
                workflow_dir,
                run_id="run-1",
                status="failed",
                change_area="semantic",
                regression_pack_status="passed",
                smoke_status="passed",
                remaining_risks=["gate B failed"],
                unresolved_warnings=["gate B failed"],
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
            self.assertEqual(payload["workflow"]["completeness"]["complete_count"], 1)
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
            self._write_complete_workflow(workflow_dir, run_id="run-2", status="passed", change_area="corpus")

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
            self._write_complete_workflow(workflow_dir, run_id="run-1", status="passed", change_area="semantic")

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
