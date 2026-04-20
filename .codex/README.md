# KindleMaster Codex Project Config

This directory holds repo-local Codex defaults for `kindleMaster`.

## Purpose

Use `.codex/config.toml` to keep workspace-specific behavior inside the repo instead of relying only on the global `~/.codex/config.toml`.

The project config should carry:
- preferred model and reasoning level for this repo,
- approval policy,
- feature toggles that are safe for this workspace,
- repo-specific tool and plugin defaults,
- the standard operational commands and restrictions for this repository.

## Why this is separate from global config

Global config is user-wide.
This directory is repository-wide.

That means:
- `~/.codex/config.toml` should keep personal and machine-wide defaults,
- `.codex/config.toml` should keep KindleMaster defaults that every collaborator or future session should inherit in this repo.

## What belongs here

Keep these kinds of settings in `.codex/config.toml`:
- `model`
- `model_reasoning_effort`
- `approval_policy`
- supported `features.*`
- repo-relevant MCP integrations
- repo-relevant plugin enablement

Keep these repo conventions synchronized between `.codex/config.toml`, `README.md`, and `AGENTS.md`:
- standard entrypoint commands,
- smoke and test defaults,
- restrictions specific to this repo,
- release and localhost freshness expectations.

## Standard operational commands

```powershell
python kindlemaster.py bootstrap
python kindlemaster.py serve
python kindlemaster.py test --suite quick
python kindlemaster.py smoke --mode quick
python kindlemaster.py validate path\to\file.epub
python kindlemaster.py audit path\to\file.epub
```

## Guardrails

- Do not place publication-specific hacks in project config.
- Do not treat project config as a replacement for `AGENTS.md`.
- Do not add unverified Codex config keys here just because they are useful conceptually.
- If a repo-standard command changes, update `.codex/config.toml`, `README.md`, and `AGENTS.md` together.
