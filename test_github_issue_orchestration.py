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
    build_issue_report,
    claim_issue,
    doctor_orchestration,
    evaluate_issue_contract,
    execute_issue,
    issue_contract_from_payload,
    load_issue_contracts,
    run_orchestration_command,
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
        self.assertIn("global_issue_template", payload["missing_files"])
        self.assertIn("orchestration_config", payload["missing_files"])
        self.assertIn("agent:ready", payload["required_labels"])

    def test_repo_has_native_and_global_orchestration_adapters(self) -> None:
        payload = doctor_orchestration(".")
        self.assertEqual(payload["status"], "passed", payload)

        config = json.loads(Path(".codex/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(config["provider"], "github_issues")
        self.assertEqual(config["native_orchestrator"]["command"], "python kindlemaster.py orchestrate")
        self.assertTrue(config["native_orchestrator"]["preferred"])
        self.assertEqual(config["issue_template"], ".github/ISSUE_TEMPLATE/kindlemaster_task.yml")
        self.assertEqual(config["compatibility"]["global_issue_template"], ".github/ISSUE_TEMPLATE/agent_task.yml")

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

    def test_load_issue_contracts_accepts_wrapped_payloads_and_string_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            issue_path = Path(temp_dir) / "issues.json"
            issue_path.write_text(
                json.dumps({"items": [{**_complete_issue(), "number": "77", "labels": ["agent:ready", "autopilot:allowed", "area:ui"]}]}),
                encoding="utf-8",
            )

            issues = load_issue_contracts(issue_path)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].number, 77)
        self.assertEqual(issues[0].labels, ("agent:ready", "area:ui", "autopilot:allowed"))

    def test_claim_execute_and_report_preserve_blockers_and_evidence(self) -> None:
        ready = issue_contract_from_payload(_complete_issue())
        blocked = issue_contract_from_payload(_complete_issue(labels=["agent:ready", "area:governance"]))

        claim_payload = claim_issue(ready)
        execute_payload = execute_issue(ready)
        blocked_claim = claim_issue(blocked)
        report = build_issue_report(blocked, evidence=["python kindlemaster.py test --suite quick"])

        self.assertEqual(claim_payload["operation"], "claim")
        self.assertFalse(claim_payload["applied"])
        self.assertEqual(execute_payload["execution_mode"], "local_agent_handoff")
        self.assertEqual(blocked_claim["status"], "blocked")
        self.assertIn("missing_label:autopilot:allowed", report["markdown"])
        self.assertIn("python kindlemaster.py test --suite quick", report["markdown"])

    def test_claim_apply_branch_reports_git_failure_without_switching_main(self) -> None:
        issue = issue_contract_from_payload(_complete_issue())

        with patch("github_issue_orchestration.subprocess.run") as run_mock:
            run_mock.return_value.returncode = 128
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = "branch exists"
            payload = claim_issue(issue, apply_branch=True, repo_root=".")

        self.assertEqual(payload["status"], "failed")
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["branch_result"]["stderr"], "branch exists")
        run_mock.assert_called_once()

    def test_run_orchestration_command_writes_report_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            issue_path = root / "issues.json"
            output_json = root / "report.json"
            output_md = root / "report.md"
            issue_path.write_text(json.dumps([_complete_issue()]), encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "orchestrate_command": "report",
                    "issues_json": str(issue_path),
                    "issue_number": 42,
                    "repo_root": ".",
                    "output_json": str(output_json),
                    "output_md": str(output_md),
                    "evidence": ["doctor passed"],
                },
            )()

            payload = run_orchestration_command(args)

            saved = json.loads(output_json.read_text(encoding="utf-8"))
            markdown = output_md.read_text(encoding="utf-8")

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(saved["issue_number"], 42)
        self.assertIn("doctor passed", markdown)

    def test_run_orchestration_command_rejects_unknown_commands_and_missing_issue_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            issue_path = root / "issues.json"
            issue_path.write_text(json.dumps([_complete_issue(), {**_complete_issue(), "number": 43}]), encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "orchestrate_command": "execute",
                    "issues_json": str(issue_path),
                    "issue_number": None,
                    "repo_root": ".",
                    "output_json": "",
                    "output_md": "",
                    "evidence": [],
                },
            )()

            with self.assertRaises(ValueError):
                run_orchestration_command(args)

            args.orchestrate_command = "unknown"
            args.issue_number = 42
            payload = run_orchestration_command(args)

        self.assertEqual(payload, {"status": "failed", "error": "unknown_orchestration_command", "command": "unknown"})


if __name__ == "__main__":
    unittest.main()
