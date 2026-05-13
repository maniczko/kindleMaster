from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


HOOKS_PATH = ".githooks"
REQUIRED_HOOKS = ("pre-commit", "pre-push")


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _repo_root(start: str | Path | None = None) -> Path:
    candidate = Path(start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "kindlemaster.py").exists():
            return path
    return Path(__file__).resolve().parents[1]


def _run_git(args: list[str], *, repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _normalize_hooks_path(value: str) -> str:
    return value.strip().strip('"').replace("\\", "/").rstrip("/")


def _read_hooks_path(repo_root: Path) -> str:
    completed = _run_git(["config", "--get", "core.hooksPath"], repo_root=repo_root)
    if completed.returncode != 0:
        return ""
    return _normalize_hooks_path(completed.stdout)


def _hook_file_status(repo_root: Path) -> dict[str, Any]:
    hooks_dir = repo_root / HOOKS_PATH
    hooks = {
        name: {
            "path": str(hooks_dir / name),
            "exists": (hooks_dir / name).is_file(),
        }
        for name in REQUIRED_HOOKS
    }
    missing = [name for name, payload in hooks.items() if not payload["exists"]]
    return {
        "hooks_dir": str(hooks_dir),
        "hooks": hooks,
        "missing_hooks": missing,
    }


def _ensure_executable(path: Path) -> None:
    if os.name == "nt":
        return
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def check_git_hooks(repo_root: str | Path | None = None) -> dict[str, Any]:
    resolved_root = _repo_root(repo_root)
    if not (resolved_root / ".git").exists():
        return {
            "status": "skipped",
            "reason": "not_git_repo",
            "repo_root": str(resolved_root),
            "hooks_path": "",
            **_hook_file_status(resolved_root),
        }

    configured_path = _read_hooks_path(resolved_root)
    files = _hook_file_status(resolved_root)
    path_ready = configured_path == HOOKS_PATH
    hooks_ready = not files["missing_hooks"]
    status_value = "installed" if path_ready and hooks_ready else "missing"
    notes: list[str] = []
    if not path_ready:
        notes.append(f"core.hooksPath is `{configured_path or '<unset>'}`, expected `{HOOKS_PATH}`.")
    if not hooks_ready:
        notes.append(f"Missing hook files: {', '.join(files['missing_hooks'])}.")

    return {
        "status": status_value,
        "reason": "" if status_value == "installed" else "not_configured",
        "repo_root": str(resolved_root),
        "hooks_path": configured_path,
        "expected_hooks_path": HOOKS_PATH,
        "notes": notes,
        **files,
    }


def install_git_hooks(repo_root: str | Path | None = None) -> dict[str, Any]:
    resolved_root = _repo_root(repo_root)
    if not (resolved_root / ".git").exists():
        return {
            "status": "skipped",
            "reason": "not_git_repo",
            "repo_root": str(resolved_root),
            "hooks_path": "",
            **_hook_file_status(resolved_root),
        }

    files = _hook_file_status(resolved_root)
    if files["missing_hooks"]:
        return {
            "status": "failed",
            "reason": "missing_hook_files",
            "repo_root": str(resolved_root),
            "expected_hooks_path": HOOKS_PATH,
            **files,
        }

    for hook_name in REQUIRED_HOOKS:
        _ensure_executable(resolved_root / HOOKS_PATH / hook_name)

    completed = _run_git(["config", "core.hooksPath", HOOKS_PATH], repo_root=resolved_root)
    if completed.returncode != 0:
        return {
            "status": "failed",
            "reason": "git_config_failed",
            "repo_root": str(resolved_root),
            "expected_hooks_path": HOOKS_PATH,
            "stderr": completed.stderr.strip(),
            **files,
        }

    result = check_git_hooks(resolved_root)
    if result["status"] == "installed":
        result["reason"] = "configured"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Install or check KindleMaster local Git hooks.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--install", action="store_true", help="Set core.hooksPath to .githooks.")
    mode.add_argument("--check", action="store_true", help="Verify hook files and core.hooksPath.")
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    args = parser.parse_args()

    payload = install_git_hooks(args.repo_root) if args.install else check_git_hooks(args.repo_root)
    print(_json_text(payload))
    return 0 if payload["status"] in {"installed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
