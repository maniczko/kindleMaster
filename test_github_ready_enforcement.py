from __future__ import annotations

import unittest
from pathlib import Path


class GithubReadyEnforcementTests(unittest.TestCase):
    def test_ready_workflow_exposes_stable_job_names_and_local_command_mapping(self) -> None:
        workflow_text = Path(".github/workflows/ready-enforcement.yml").read_text(encoding="utf-8")

        self.assertIn("name: READY Enforcement", workflow_text)
        self.assertIn("ready-quick:", workflow_text)
        self.assertIn("ready-release:", workflow_text)
        self.assertIn("ready-gate:", workflow_text)
        self.assertIn("python kindlemaster.py test --suite quick", workflow_text)
        self.assertIn("python kindlemaster.py test --suite release", workflow_text)
        self.assertIn("needs:", workflow_text)
        self.assertIn("- ready-quick", workflow_text)
        self.assertIn("- ready-release", workflow_text)

    def test_ready_doc_matches_workflow_and_branch_protection_contract(self) -> None:
        workflow_text = Path(".github/workflows/ready-enforcement.yml").read_text(encoding="utf-8")
        doc_text = Path("docs/github-ready-enforcement.md").read_text(encoding="utf-8")

        self.assertIn(".github/workflows/ready-enforcement.yml", doc_text)
        self.assertIn("ready-gate", doc_text)
        self.assertIn("python kindlemaster.py test --suite quick", doc_text)
        self.assertIn("python kindlemaster.py test --suite release", doc_text)
        self.assertIn("ready-gate", workflow_text)


if __name__ == "__main__":
    unittest.main()
