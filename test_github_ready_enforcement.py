from __future__ import annotations

import unittest
from pathlib import Path


class GithubReadyEnforcementTests(unittest.TestCase):
    def test_ready_workflow_exposes_stable_job_names_and_local_command_mapping(self) -> None:
        workflow_text = Path(".github/workflows/ready-enforcement.yml").read_text(encoding="utf-8")

        self.assertIn("name: READY Enforcement", workflow_text)
        self.assertIn("ready-governance:", workflow_text)
        self.assertIn("ready-quick:", workflow_text)
        self.assertIn("ready-release:", workflow_text)
        self.assertIn("ready-gate:", workflow_text)
        self.assertIn('python-version: "3.12"', workflow_text)
        self.assertIn('python-version: "3.13"', workflow_text)
        self.assertIn('python-version: "3.14"', workflow_text)
        self.assertIn("windows-latest", workflow_text)
        self.assertIn("python kindlemaster.py test --suite quick", workflow_text)
        self.assertIn("python -m unittest test_sprint1_quality_gates.py", workflow_text)
        self.assertIn("pnpm run test:contracts:regression", workflow_text)
        self.assertIn("python kindlemaster.py test --suite release", workflow_text)
        self.assertIn("needs:", workflow_text)
        self.assertIn("- ready-governance", workflow_text)
        self.assertIn("- ready-quick", workflow_text)
        self.assertIn("- ready-release", workflow_text)

    def test_ready_workflow_enforces_static_security_coverage_and_artifacts(self) -> None:
        workflow_text = Path(".github/workflows/ready-enforcement.yml").read_text(encoding="utf-8")
        requirements_dev_text = Path("requirements-dev.txt").read_text(encoding="utf-8")

        self.assertIn("python -m ruff check --select E9,F63,F7,F82", workflow_text)
        self.assertIn("kindlemaster.py github_issue_orchestration.py premium_tools.py scripts", workflow_text)
        self.assertIn("test_github_issue_orchestration.py", workflow_text)
        self.assertIn("python -m pip check", workflow_text)
        self.assertIn("python -m pip_audit -r requirements.txt -r requirements-dev.txt --progress-spinner off --timeout 60", workflow_text)
        self.assertIn("python -m coverage run -m unittest", workflow_text)
        self.assertIn('--include="kindlemaster.py,github_issue_orchestration.py,scripts/generate_project_status.py"', workflow_text)
        self.assertIn("--fail-under=${{ env.GOVERNANCE_COVERAGE_FAIL_UNDER }}", workflow_text)
        self.assertIn("GOVERNANCE_COVERAGE_FAIL_UNDER: \"75\"", workflow_text)
        self.assertIn("CORE_CONVERSION_COVERAGE_FAIL_UNDER: \"70\"", workflow_text)
        self.assertIn("CONVERTER_COVERAGE_FAIL_UNDER: \"60\"", workflow_text)
        self.assertIn("TEXT_NORMALIZATION_COVERAGE_FAIL_UNDER: \"65\"", workflow_text)
        self.assertIn("SEMANTIC_CLEANUP_COVERAGE_FAIL_UNDER: \"70\"", workflow_text)
        self.assertIn("python kindlemaster.py test --suite quality-critical", workflow_text)
        self.assertIn("npm run test:coverage", workflow_text)
        self.assertIn("core-conversion-coverage-${{ matrix.os }}-py${{ matrix.python-version }}.xml", workflow_text)
        self.assertIn("actions/upload-artifact@v7", workflow_text)
        self.assertIn("reports/coverage/", workflow_text)
        self.assertIn("ready-quick-evidence", workflow_text)
        self.assertIn("ready-release-evidence", workflow_text)
        self.assertIn("ruff", requirements_dev_text)
        self.assertIn("pip-audit", requirements_dev_text)

    def test_ready_doc_matches_workflow_and_branch_protection_contract(self) -> None:
        workflow_text = Path(".github/workflows/ready-enforcement.yml").read_text(encoding="utf-8")
        doc_text = Path("docs/github-ready-enforcement.md").read_text(encoding="utf-8")
        matrix_text = Path("docs/toolchain-matrix.md").read_text(encoding="utf-8")

        self.assertIn(".github/workflows/ready-enforcement.yml", doc_text)
        self.assertIn("ready-governance", doc_text)
        self.assertIn("ready-gate", doc_text)
        self.assertIn("python kindlemaster.py test --suite quick", doc_text)
        self.assertIn("python kindlemaster.py test --suite release", doc_text)
        self.assertIn("static-quality", doc_text)
        self.assertIn("control-plane", doc_text)
        self.assertIn("pip-audit", doc_text)
        self.assertIn("coverage", doc_text)
        self.assertIn("core conversion coverage", doc_text)
        self.assertIn("Chess FEN Corpus Gate", doc_text)
        self.assertIn("--fen-min-profile-count 1", doc_text)
        self.assertIn("false_positive_count == 0", doc_text)
        self.assertIn("Sprint 1 QA regression tests", doc_text)
        self.assertIn("pnpm run test:contracts:regression", doc_text)
        self.assertIn("npm run test:coverage", doc_text)
        self.assertIn("Python 3.12, 3.13, and 3.14", doc_text)
        self.assertIn("governance artifacts", doc_text)
        self.assertIn("ready-gate", workflow_text)
        self.assertIn("Python 3.12, 3.13, and 3.14", matrix_text)
        self.assertIn("test_sprint1_quality_gates.py", matrix_text)
        self.assertIn("pnpm run test:contracts:regression", matrix_text)
        self.assertIn("Windows canary", matrix_text)


if __name__ == "__main__":
    unittest.main()
