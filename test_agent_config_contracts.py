from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import premium_tools


class AgentConfigContractTests(unittest.TestCase):
    def test_codex_config_keeps_pinned_agent_tooling(self) -> None:
        config_text = Path(".codex/config.toml").read_text(encoding="utf-8")

        self.assertIn('model = "gpt-5.5"', config_text)
        self.assertIn('model_reasoning_effort = "xhigh"', config_text)
        self.assertIn('approval_policy = "on-request"', config_text)
        self.assertIn("@playwright/mcp@0.0.70", config_text)
        self.assertNotIn("@playwright/mcp@latest", config_text)
        for plugin in [
            '[plugins."github@openai-curated"]',
            '[plugins."linear@openai-curated"]',
            '[plugins."build-web-apps@openai-curated"]',
            '[plugins."browser-use@openai-bundled"]',
        ]:
            self.assertIn(plugin, config_text)

    def test_tracked_git_hooks_define_balanced_governance_gates(self) -> None:
        pre_commit = Path(".githooks/pre-commit")
        pre_push = Path(".githooks/pre-push")

        self.assertTrue(pre_commit.exists())
        self.assertTrue(pre_push.exists())

        pre_commit_text = pre_commit.read_text(encoding="utf-8")
        pre_push_text = pre_push.read_text(encoding="utf-8")

        self.assertIn("python -m ruff check --select E9,F63,F7,F82", pre_commit_text)
        self.assertIn("test_agent_config_contracts.py", pre_commit_text)
        self.assertIn("test_skill_contracts.py", pre_commit_text)
        self.assertIn("test_github_ready_enforcement.py", pre_commit_text)
        self.assertIn("python kindlemaster.py test --suite quick", pre_push_text)
        self.assertIn("python kindlemaster.py status", pre_push_text)
        self.assertNotIn("test --suite release", pre_push_text)
        self.assertNotIn("test --suite corpus", pre_push_text)

    def test_install_git_hooks_script_exposes_check_mode(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/install_git_hooks.py", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("--install", completed.stdout)
        self.assertIn("--check", completed.stdout)

    def test_claude_example_uses_current_kindlemaster_runtime_contract(self) -> None:
        example_path = Path(".claude/settings.example.json")
        ignore_text = Path(".gitignore").read_text(encoding="utf-8")

        self.assertTrue(example_path.exists())
        payload = json.loads(example_path.read_text(encoding="utf-8"))
        rendered = json.dumps(payload)

        self.assertIn("python kindlemaster.py serve", rendered)
        self.assertIn("http://127.0.0.1:5001/", rendered)
        self.assertNotIn("python app.py", rendered)
        self.assertNotIn("localhost:5000", rendered)
        self.assertIn(".claude/settings.local.json", ignore_text)
        self.assertIn("!.claude/settings.example.json", ignore_text)

    def test_agent_readiness_detects_codex_hooks_skills_and_claude_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            home_root = repo_root / "home"
            skills_root = home_root / ".codex" / "skills"
            for skill_name in premium_tools.KINDLEMASTER_SKILL_NAMES:
                (skills_root / skill_name).mkdir(parents=True)

            (repo_root / ".codex").mkdir()
            (repo_root / ".codex" / "config.toml").write_text(
                "\n".join(
                    [
                        'model = "gpt-5.5"',
                        'model_reasoning_effort = "xhigh"',
                        'approval_policy = "on-request"',
                        "[features]",
                        "multi_agent = true",
                        "[mcp_servers.playwright]",
                        'command = "npx"',
                        'args = ["@playwright/mcp@0.0.70"]',
                        '[plugins."github@openai-curated"]',
                        "enabled = true",
                        '[plugins."linear@openai-curated"]',
                        "enabled = true",
                        '[plugins."build-web-apps@openai-curated"]',
                        "enabled = true",
                        '[plugins."browser-use@openai-bundled"]',
                        "enabled = true",
                    ]
                ),
                encoding="utf-8",
            )
            (repo_root / ".githooks").mkdir()
            (repo_root / ".githooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
            (repo_root / ".githooks" / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
            (repo_root / ".claude").mkdir()
            (repo_root / ".claude" / "settings.local.json").write_text(
                '{"permissions":{"allow":["Bash(python kindlemaster.py serve)","Bash(curl http://127.0.0.1:5001/)"]}}',
                encoding="utf-8",
            )

            with patch("premium_tools.Path.home", return_value=home_root):
                with patch("premium_tools._read_git_hooks_path", return_value=".githooks"):
                    readiness = premium_tools.detect_agent_readiness(repo_root=repo_root)

        self.assertEqual(readiness["status"], "supported")
        self.assertEqual(readiness["checks"]["codex_config"]["status"], "supported")
        self.assertEqual(readiness["checks"]["playwright_mcp_pin"]["status"], "supported")
        self.assertEqual(readiness["checks"]["plugins"]["status"], "supported")
        self.assertEqual(readiness["checks"]["skills"]["status"], "supported")
        self.assertEqual(readiness["checks"]["git_hooks"]["status"], "supported")
        self.assertEqual(readiness["checks"]["claude_local_settings"]["status"], "supported")


if __name__ == "__main__":
    unittest.main()
