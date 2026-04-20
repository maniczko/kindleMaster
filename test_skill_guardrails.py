from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


SKILLS_ROOT = Path.home() / ".codex" / "skills"

SKILL_NAMES = [
    "kindlemaster-epub-release-auditor",
    "kindlemaster-reference-repair",
    "kindlemaster-heading-toc-recovery",
    "kindlemaster-text-normalization-pl-en",
    "kindlemaster-corpus-smoke",
    "kindlemaster-workflow-operator",
    "kindlemaster-ui-runtime-debug",
]

BANNED_SAMPLE_TOKENS = [
    "babok",
    "allegro",
    "woodpecker",
    "material sponsorowany",
]

WRAPPER_SKILLS = {
    "kindlemaster-epub-release-auditor": "scripts/run_release_audit.py",
    "kindlemaster-reference-repair": "scripts/run_reference_repair.py",
    "kindlemaster-heading-toc-recovery": "scripts/run_heading_toc_repair.py",
    "kindlemaster-text-normalization-pl-en": "scripts/run_text_cleanup.py",
    "kindlemaster-corpus-smoke": "scripts/run_corpus_smoke.py",
}


class SkillGuardrailTests(unittest.TestCase):
    def test_skill_markdown_has_no_sample_specific_runtime_bias(self) -> None:
        for name in SKILL_NAMES:
            skill_md = SKILLS_ROOT / name / "SKILL.md"
            content = skill_md.read_text(encoding="utf-8").lower()
            with self.subTest(skill=name):
                for token in BANNED_SAMPLE_TOKENS:
                    self.assertNotIn(token, content, f"{name} should stay generic and not depend on sample token {token!r}")

    def test_skill_files_are_utf8_clean(self) -> None:
        suspicious_fragments = ["Ã", "â", "\ufffd"]
        for name in SKILL_NAMES:
            skill_dir = SKILLS_ROOT / name
            for path in skill_dir.rglob("*"):
                if path.is_file() and path.suffix in {".md", ".yaml", ".py"}:
                    content = path.read_text(encoding="utf-8")
                    with self.subTest(path=str(path)):
                        for fragment in suspicious_fragments:
                            self.assertNotIn(fragment, content, f"Possible mojibake in {path}: {fragment!r}")

    def test_wrapper_scripts_expose_help(self) -> None:
        for name, relative_path in WRAPPER_SKILLS.items():
            script_path = SKILLS_ROOT / name / relative_path
            with self.subTest(skill=name):
                completed = subprocess.run(
                    [sys.executable, str(script_path), "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
                self.assertIn("usage", (completed.stdout or "").lower())


if __name__ == "__main__":
    unittest.main()
