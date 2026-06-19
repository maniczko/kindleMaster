from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from github_issue_orchestration import (
    branch_name_for_issue,
    doctor_orchestration,
    evaluate_issue_contract,
    issue_contract_from_payload,
    sync_issues,
)


def _complete_issue(*, labels: list[str] | None = None) -> dict[str, object]:
    return {
        "number": 42,
        "title": "Improve semantic cleanup orchestration",
        "state": "open",
        "html_url": "https://github.com/example/kindlemaster/issues/42",
        "labels": [{"name": label} for label in (labels or ["agent:ready", "autopilot:allowed", "area:semantic"])],
        "body": "\n".join(
            [
                "# Cel",
                "Improve the real runtime path.",
                "# Kontekst",
                "KindleMaster conversion quality.",
                "# Zakres",
                "Only semantic cleanup orchestration.",
                "# Kryteria akceptacji",
                "- Quality gate stays visible.",
                "# Walidacja",
                "- python kindlemaster.py test --suite quality-critical",
                "# Raport koncowy",
                "Summarize evidence and risks.",
            ]
        ),
    }


class GithubIssueOrchestrationTests(unittest.TestCase):
    def test_complete_issue_contract_is_ready_and_maps_area_to_quality_gate(self) -> None:
        issue = issue_contract_from_payload(_complete_issue())

        payload = evaluate_issue_contract(issue)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["missing_sections"], [])
        self.assertEqual(payload["missing_labels"], [])
        self.assertEqual(payload["area_labels"], ["area:semantic"])
        self.assertEqual(payload["gate_labels"], ["gate:quality-critical"])
        self.assertIn("python kindlemaster.py test --suite quality-critical", payload["recommended_commands"])
        self.assertTrue(payload["workflow_baseline_required"])
        self.assertEqual(payload["branch"], "codex/issue-42-improve-semantic-cleanup-orchestration")

    def test_missing_autopilot_contract_blocks_execution(self) -> None:
        issue = issue_contract_from_payload(
            _complete_issue(labels=["agent:ready", "autopilot:requires-human", "area:ui"])
        )

        payload = evaluate_issue_contract(issue)

        self.assertEqual(payload["status"], "blocked")
        self.assertIn("autopilot:allowed", payload["missing_labels"])
        self.assertIn("autopilot:requires-human", payload["blocking_labels"])
        self.assertIn("missing_label:autopilot:allowed", payload["blockers"])
        self.assertIn("blocking_label:autopilot:requires-human", payload["blockers"])

    def test_explicit_gate_labels_override_area_defaults(self) -> None:
        issue = issue_contract_from_payload(
            _complete_issue(labels=["agent:ready", "autopilot:allowed", "area:ui", "gate:release"])
        )

        payload = evaluate_issue_contract(issue)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["gate_labels"], ["gate:release"])
        self.assertEqual(payload["recommended_commands"], ["python kindlemaster.py test --suite release"])

    def test_sync_summarizes_ready_and_blocked_issues(self) -> None:
        ready = issue_contract_from_payload(_complete_issue())
        blocked = issue_contract_from_payload(_complete_issue(labels=["agent:ready", "area:governance"]))

        payload = sync_issues([ready, blocked])

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["summary"], {"total": 2, "ready": 1, "blocked": 1})

    def test_branch_slug_is_stable_for_missing_issue_number(self) -> None:
        self.assertEqual(branch_name_for_issue(None, "Fix EPUB / Kindle delivery!"), "codex/issue-unknown-fix-epub-kindle-delivery")

    def test_doctor_reports_missing_autopilot_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = doctor_orchestration(temp_dir)

        self.assertEqual(payload["status"], "failed")
        self.assertIn("issue_template", payload["missing_files"])
        self.assertIn("agent:ready", payload["required_labels"])

    def test_kindlemaster_orchestrate_sync_routes_to_issue_validator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_path = Path(temp_dir) / "issues.json"
            issue_path.write_text(json.dumps([_complete_issue()]), encoding="utf-8")
            stdout = io.StringIO()
            with patch.object(sys, "argv", ["kindlemaster.py", "orchestrate", "sync", "--issues-json", str(issue_path)]):
                with contextlib.redirect_stdout(stdout):
                    exit_code = kindlemaster.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["summary"]["ready"], 1)
        self.assertEqual(payload["issues"][0]["branch"], "codex/issue-42-improve-semantic-cleanup-orchestration")


if __name__ == "__main__":
    unittest.main()
