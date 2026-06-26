# GitHub Autopilot Orchestration

GitHub Issues are the task truth for agent-executable KindleMaster work. Markdown files in this repo are policy, templates, and runbooks only; they are not the backlog.

## Operating Model

- An issue is executable only when it has `agent:ready`, `autopilot:allowed`, one `area:*` label, and the required sections from `.github/ISSUE_TEMPLATE/kindlemaster_task.yml`.
- `.github/ISSUE_TEMPLATE/agent_task.yml` is a compatibility alias for the generic Codex GitHub Issue orchestrator; do not treat it as a separate backlog contract.
- `.codex/orchestration.json` mirrors the native contract for global tooling. If it disagrees with `python kindlemaster.py orchestrate`, update the native orchestrator first and then update the mirror.
- `autopilot:requires-human`, `agent:blocked`, or `needs-product-decision` blocks execution.
- The local command `python kindlemaster.py orchestrate` validates issue contracts and prepares branch, gate, and report payloads.
- The generic global doctor may be used as a compatibility check, but `python kindlemaster.py orchestrate` remains the preferred KindleMaster orchestrator.
- GitHub Actions remains a validation and READY evidence surface. It does not autonomously edit code in v1.

## Required Labels

Agent state:
- `agent:ready`
- `agent:claimed`
- `agent:blocked`
- `agent:needs-fix`
- `agent:needs-review`
- `agent:done`

Autopilot policy:
- `autopilot:allowed`
- `autopilot:requires-human`

Area:
- `area:app`
- `area:converter`
- `area:semantic`
- `area:text`
- `area:ui`
- `area:delivery`
- `area:auth`
- `area:corpus`
- `area:governance`

Gate:
- `gate:quick`
- `gate:quality-critical`
- `gate:browser`
- `gate:runtime`
- `gate:release`
- `gate:corpus`

## CLI

```powershell
python kindlemaster.py orchestrate doctor
python kindlemaster.py orchestrate sync --issues-json reports/github/issues.json
python kindlemaster.py orchestrate claim --issues-json reports/github/issues.json --issue-number 123
python kindlemaster.py orchestrate execute --issues-json reports/github/issues.json --issue-number 123
python kindlemaster.py orchestrate report --issues-json reports/github/issues.json --issue-number 123 --output-md reports/github/issue-123-report.md
```

`claim --apply-branch` may create `codex/issue-<number>-<slug>` locally. Use it only after checking that the issue is ready and unrelated local changes are understood.

## Issue to PR Flow

1. Create an issue with the KindleMaster task template.
2. Add `autopilot:allowed` only when the issue is complete enough to execute.
3. Run `python kindlemaster.py orchestrate sync --issues-json <file>` or the equivalent future GitHub API wrapper.
4. Claim a ready issue and create a `codex/issue-...` branch.
5. Execute the issue in one coherent PR.
6. Run the commands recommended by the issue gates.
7. Attach test results, workflow artifacts, and residual risks to the PR.
8. Let READY enforcement block merge when evidence is missing.

## Guardrails

- Never commit directly to `main`.
- Do not execute issues missing acceptance criteria or validation.
- Keep one issue per branch and one branch per PR unless an epic explicitly says otherwise.
- For conversion-quality changes, prefer `workflow baseline/verify` evidence when a concrete fixture exists.
- Do not put secrets, raw email addresses, tokens, or private document content into issues or PRs.
