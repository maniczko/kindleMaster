from __future__ import annotations

import unittest
from pathlib import Path


SKILLS_ROOT = Path.home() / ".codex" / "skills"

COMMON_SECTIONS = [
    "## Primary Goal",
    "## Use This Skill When",
    "## Do Not Use This Skill When",
    "## Use With",
    "## Default Workflow",
    "## Required Artifacts",
    "## Regression Pack",
    "## Quality Gates",
    "## Generalization Guardrails",
    "## Escalate When",
    "## Done Criteria",
]

SKILLS = [
    {
        "name": "kindlemaster-epub-release-auditor",
        "scripts": ["scripts/run_release_audit.py"],
        "workflow_required": True,
    },
    {
        "name": "kindlemaster-reference-repair",
        "scripts": ["scripts/run_reference_repair.py"],
        "workflow_required": True,
    },
    {
        "name": "kindlemaster-heading-toc-recovery",
        "scripts": ["scripts/run_heading_toc_repair.py"],
        "workflow_required": True,
    },
    {
        "name": "kindlemaster-text-normalization-pl-en",
        "scripts": ["scripts/run_text_cleanup.py"],
        "workflow_required": True,
    },
    {
        "name": "kindlemaster-corpus-smoke",
        "scripts": ["scripts/run_corpus_smoke.py"],
        "workflow_required": False,
    },
    {
        "name": "kindlemaster-workflow-operator",
        "scripts": [],
        "workflow_required": True,
    },
    {
        "name": "kindlemaster-ui-runtime-debug",
        "scripts": [],
        "workflow_required": False,
    },
]


class SkillContractTests(unittest.TestCase):
    def test_skill_files_and_sections_exist(self) -> None:
        for skill in SKILLS:
            skill_dir = SKILLS_ROOT / skill["name"]
            with self.subTest(skill=skill["name"]):
                self.assertTrue(skill_dir.exists(), f"Missing skill directory: {skill_dir}")
                skill_md = skill_dir / "SKILL.md"
                openai_yaml = skill_dir / "agents" / "openai.yaml"
                self.assertTrue(skill_md.exists(), f"Missing SKILL.md for {skill['name']}")
                self.assertTrue(openai_yaml.exists(), f"Missing openai.yaml for {skill['name']}")
                content = skill_md.read_text(encoding="utf-8")
                self.assertIn(f"name: {skill['name']}", content)
                for section in COMMON_SECTIONS:
                    self.assertIn(section, content, f"{skill['name']} missing section {section}")
                if skill["workflow_required"]:
                    self.assertIn("python kindlemaster.py workflow baseline", content)
                    self.assertIn("python kindlemaster.py workflow verify", content)
                for relative_script in skill["scripts"]:
                    self.assertTrue((skill_dir / relative_script).exists(), f"Missing {relative_script} for {skill['name']}")

    def test_openai_yaml_contains_routing_fields(self) -> None:
        for skill in SKILLS:
            openai_yaml = SKILLS_ROOT / skill["name"] / "agents" / "openai.yaml"
            with self.subTest(skill=skill["name"]):
                content = openai_yaml.read_text(encoding="utf-8")
                self.assertIn("display_name:", content)
                self.assertIn("short_description:", content)
                self.assertIn("default_prompt:", content)
                self.assertIn(f"${skill['name']}", content)
                self.assertIn("allow_implicit_invocation: true", content)


if __name__ == "__main__":
    unittest.main()
