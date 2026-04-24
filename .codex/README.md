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

Preferred browser URL for this repo: `http://kindlemaster.localhost:5001/`.
Loopback bind remains `127.0.0.1:5001` for runtime safety and tool fallback.

## Authority Map

- `kindlemaster.py` is the executable source of truth for the CLI command surface.
- `AGENTS.md` owns the canonical human-readable control-plane source-of-truth matrix for command policy, workflow artifacts, and authoritative versus derived docs.
- `docs/toolchain-matrix.md` owns supported local toolchain expectations for `test --suite` lanes.
- `.codex/config.toml` owns active repo-local Codex settings only; its comments are convenience mirrors of the command surface and policy.
- Generated files under `reports/` and `output/` are derived runtime artifacts, never governance authority.

## Convenience Command Mirror

This is a quick operator-facing excerpt. If it ever disagrees with `kindlemaster.py` or `AGENTS.md`, those sources win and this file should be updated.

```powershell
python kindlemaster.py bootstrap
python kindlemaster.py doctor
python kindlemaster.py prepare-reference-inputs
python kindlemaster.py serve
python kindlemaster.py convert path\to\input.docx --output output\result.epub
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite corpus
python kindlemaster.py status
python kindlemaster.py test --suite browser
python kindlemaster.py test --suite runtime
python kindlemaster.py smoke --mode quick
python kindlemaster.py validate path\to\file.epub
python kindlemaster.py audit path\to\file.epub
python kindlemaster.py workflow baseline path\to\input.pdf --change-area reference
python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>
```

## Guardrails

- Do not place publication-specific hacks in project config.
- Do not treat project config as a replacement for `AGENTS.md`.
- Do not treat this README or `.codex/config.toml` comments as a second policy source.
- Do not add unverified Codex config keys here just because they are useful conceptually.
- If a repo-standard command changes, update `.codex/config.toml`, `README.md`, and `AGENTS.md` together.
