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
            '[plugins."openai-developers@openai-curated"]',
        ]:
            self.assertIn(plugin, config_text)

    def test_agents_defines_plugin_auto_routing_policy_markers(self) -> None:
        agents_text = Path("AGENTS.md").read_text(encoding="utf-8")

        for marker in premium_tools.PLUGIN_ROUTING_POLICY_MARKERS:
            self.assertIn(marker, agents_text)

    def test_prompt_engineering_policy_and_repo_skill_are_present(self) -> None:
        agents_text = Path("AGENTS.md").read_text(encoding="utf-8")
        config_text = Path(".codex/config.toml").read_text(encoding="utf-8")
        codex_readme = Path(".codex/README.md").read_text(encoding="utf-8")
        root_readme = Path("README.md").read_text(encoding="utf-8")
        repo_skill_path = Path(".codex/skills/prompt-engineer/SKILL.md")

        for marker in premium_tools.PROMPT_ENGINEERING_POLICY_MARKERS:
            self.assertIn(marker, agents_text)

        self.assertTrue(repo_skill_path.exists())
        repo_skill = repo_skill_path.read_text(encoding="utf-8")
        for marker in premium_tools.PROMPT_ENGINEER_SKILL_MARKERS:
            self.assertIn(marker, repo_skill)

        for mirror_text in [config_text, codex_readme, root_readme]:
            self.assertIn("prompt-engineer", mirror_text)
            self.assertIn("Prompt -> Review -> Rewrite -> Execute", mirror_text)
            self.assertIn("TRYB: DEBUG", mirror_text)
            self.assertIn("TRYB: EPUB QUALITY AUDIT", mirror_text)
            self.assertIn("agent_quality_gate", mirror_text)
            self.assertIn("9.0", mirror_text)

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
        self.assertIn("test_github_issue_orchestration.py", pre_commit_text)
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
            (skills_root / "prompt-engineer").mkdir(parents=True)
            (skills_root / "prompt-engineer" / "SKILL.md").write_text(
                "\n".join(premium_tools.PROMPT_ENGINEER_SKILL_MARKERS),
                encoding="utf-8",
            )

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
                        '[plugins."openai-developers@openai-curated"]',
                        "enabled = true",
                    ]
                ),
                encoding="utf-8",
            )
            (repo_root / ".githooks").mkdir()
            (repo_root / ".githooks" / "pre-commit").write_text(
                "\n".join(
                    [
                        "#!/bin/sh",
                        "python -m unittest test_agent_config_contracts.py test_skill_contracts.py test_skill_guardrails.py",
                    ]
                ),
                encoding="utf-8",
            )
            (repo_root / ".githooks" / "pre-push").write_text(
                "\n".join(["#!/bin/sh", "python kindlemaster.py test --suite quick", "python kindlemaster.py status"]),
                encoding="utf-8",
            )
            (repo_root / "AGENTS.md").write_text(
                "\n".join(
                    list(premium_tools.PLUGIN_ROUTING_POLICY_MARKERS)
                    + list(premium_tools.PROMPT_ENGINEERING_POLICY_MARKERS)
                ),
                encoding="utf-8",
            )
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
        self.assertEqual(readiness["checks"]["plugin_routing_policy"]["status"], "supported")
        self.assertEqual(readiness["checks"]["prompt_engineering_policy"]["status"], "supported")
        self.assertEqual(readiness["checks"]["prompt_engineer_skill"]["status"], "supported")
        self.assertEqual(readiness["checks"]["skills"]["status"], "supported")
        self.assertEqual(readiness["checks"]["git_hooks"]["status"], "supported")
        self.assertEqual(readiness["checks"]["claude_local_settings"]["status"], "supported")

        quality_gate = readiness["quality_gate"]
        self.assertEqual(quality_gate["status"], "supported")
        self.assertGreaterEqual(quality_gate["average_score"], 9.0)
        self.assertEqual(quality_gate["threshold"], 9.0)
        self.assertEqual(quality_gate["missing_actions"], [])
        for category in [
            "codex_config_and_tools",
            "plugin_auto_routing",
            "prompt_normalization_modes",
            "installed_skills",
            "governance_hooks",
            "local_session_drift",
        ]:
            self.assertIn(category, quality_gate["categories"])
            self.assertGreaterEqual(quality_gate["categories"][category]["score"], 9.0)


if __name__ == "__main__":
    unittest.main()
