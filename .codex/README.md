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

Current repo-local defaults:
- model: `gpt-5.5`
- reasoning: `xhigh`
- approval policy: `on-request`
- multi-agent work: enabled
- plugin enablement: GitHub, Linear as optional mirror, Build Web Apps, Browser Use, and OpenAI Developers
- browser verification: Browser Use plugin plus pinned Playwright MCP `@playwright/mcp@0.0.70`
- local Git hook path: `.githooks`
- agent readiness surface: `python kindlemaster.py doctor` reports `agent_readiness.quality_gate` and `agent_readiness.checks.agent_quality_gate`

Model routing convention:
- use `gpt-5.5` with high reasoning for conversion runtime, EPUB integrity, chess FEN/PGN, corpus/release gates, deployment architecture, and cross-module debugging,
- use a lighter/mini model for status checks, summaries, token/cost reports, simple command lookups, and low-risk documentation-only answers when model selection is available,
- use a mid-tier model for bounded single-file fixes, UI copy/layout polish, and report formatting when no EPUB/FEN/release invariant is at risk,
- after a merge or large milestone, prefer a fresh thread or compact handoff to avoid carrying a large stale context into simple turns,
- escalate back to the strongest available model whenever a lighter-model turn uncovers runtime, FEN/PGN, package integrity, or release-gate risk.

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
- local hook policy and governance evidence lanes.
- plugin auto-routing mirrors from `AGENTS.md` Section 34A.
- prompt auto-normalization mirrors from `AGENTS.md` Section 34B and `.codex/skills/prompt-engineer/SKILL.md`.

Preferred browser URL for this repo: `http://kindlemaster.localhost:5001/`.
Loopback bind remains `127.0.0.1:5001` for runtime safety and tool fallback.

## Plugin Auto-Routing Mirror

`AGENTS.md` Section 34A is authoritative. In short:
- use Browser Use for local UI/runtime verification and localhost freshness checks,
- use GitHub for requested branch, commit, PR, CI, review, and issue-backed autopilot workflows,
- use Linear only when explicitly requested as a mirror or historical VAT planning surface,
- use OpenAI Developers only for OpenAI API, key setup, provider configuration, Agents SDK, ChatGPT Apps, or `ai_quality`,
- use Build Web Apps for React/Vite UI, frontend design, and browser-facing UI tests.

Do not add speculative plugin keys here unless Codex supports them as real config.

## Prompt Auto-Normalization Mirror

`AGENTS.md` Section 34B and the `prompt-engineer` skill are authoritative. In short:
- use `prompt-engineer` when a prompt is large, ambiguous, high-impact, multi-step, or missing acceptance criteria, validation, scope, risks, or output format,
- follow `Prompt -> Review -> Rewrite -> Execute` for actionable implementation prompts,
- support explicit work modes: `TRYB: DEBUG`, `TRYB: IMPLEMENT`, `TRYB: REVIEW`, `TRYB: AUDIT`, `TRYB: UI POLISH`, and `TRYB: EPUB QUALITY AUDIT`,
- normalize Polish prompts to `Cel`, `Kontekst`, `Zakres`, `Kryteria akceptacji`, `Walidacja`, and `Raport końcowy`,
- skip formal rewriting for trivial requests or when the user explicitly asks not to rewrite the prompt.

This is an instruction-level policy. Do not add speculative TOML keys for prompt rewriting unless Codex supports them as real runtime settings.

## Agent Quality Gate

`python kindlemaster.py doctor` reports `agent_readiness.quality_gate` with threshold `9.0`.

The gate scores:
- Codex config and pinned tooling,
- plugin auto-routing,
- prompt normalization modes,
- installed KindleMaster skills,
- governance hooks,
- local session drift.

If the average drops below `9.0`, repair `missing_actions` before treating the agent setup as production-ready.

## Authority Map

- `kindlemaster.py` is the executable source of truth for the CLI command surface.
- `AGENTS.md` owns the canonical human-readable control-plane source-of-truth matrix for command policy, workflow artifacts, and authoritative versus derived docs.
- `docs/toolchain-matrix.md` owns supported local toolchain expectations for `test --suite` lanes.
- GitHub Issues plus `.github/ISSUE_TEMPLATE/kindlemaster_task.yml` own agent-executable task truth.
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
python kindlemaster.py test --suite quality-critical
python kindlemaster.py test --suite corpus
python kindlemaster.py status
python kindlemaster.py ml dataset
python kindlemaster.py ml train
python kindlemaster.py ml evaluate
python kindlemaster.py test --suite browser
python kindlemaster.py test --suite runtime
python kindlemaster.py test --suite release
python kindlemaster.py smoke --mode quick
python kindlemaster.py validate path\to\file.epub
python kindlemaster.py audit path\to\file.epub
python kindlemaster.py workflow baseline path\to\input.pdf --change-area reference
python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>
python kindlemaster.py orchestrate doctor
python kindlemaster.py orchestrate sync --issues-json reports/github/issues.json
python scripts/install_git_hooks.py --check
python scripts/install_git_hooks.py --install
```

## Governance Evidence

The standard local evidence files are generated, not hand maintained:

```text
reports/governance/doctor.json
reports/governance/quick.json
reports/governance/quality-critical.json
reports/governance/release.json
```

Each file records `generated_at`, `command`, `status`, `returncode`, `elapsed_seconds`, and `notes`. The project status dashboard reads these files for freshness warnings.

## Guardrails

- Do not place publication-specific hacks in project config.
- Do not treat project config as a replacement for `AGENTS.md`.
- Do not treat this README or `.codex/config.toml` comments as a second policy source.
- Do not add unverified Codex config keys here just because they are useful conceptually.
- Do not use floating MCP versions such as `@latest`; pin tool versions and update deliberately.
- If a repo-standard command changes, update `.codex/config.toml`, `README.md`, and `AGENTS.md` together.
