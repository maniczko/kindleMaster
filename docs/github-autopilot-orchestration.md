# GitHub Autopilot Orchestration

GitHub Issues are the task truth for agent-executable KindleMaster work. Markdown files in this repo are policy, templates, and runbooks only; they are not the backlog.

## Operating Model

- An issue is executable only when it has `agent:ready`, `autopilot:allowed`, one `area:*` label, and the required sections from `.github/ISSUE_TEMPLATE/kindlemaster_task.yml`.
- `.github/ISSUE_TEMPLATE/agent_task.yml` is a compatibility alias for the generic Codex GitHub Issue orchestrator; do not treat it as a separate backlog contract.
- `.codex/orchestration.json` mirrors the native contract for global tooling. If it disagrees with `python kindlemaster.py orchestrate`, update the native orchestrator first and then update the mirror.
- `autopilot:requires-human`, `agent:blocked`, `agent:needs-review`, `needs-product-decision`, or `training-data:missing` blocks execution for that issue only.
- Missing training, benchmark, corpus, fixture, or holdout data must be reported separately as `TRAINING_DATA_GAP` and must not block unrelated ready issues.
- The local command `python kindlemaster.py orchestrate` validates issue contracts and prepares branch, gate, and report payloads.
- The generic global doctor may be used as a compatibility check, but `python kindlemaster.py orchestrate` remains the preferred KindleMaster orchestrator.
- `.github/workflows/codex-issue-queue.yml` serializes the GitHub-side queue: one run selects at most one ready issue, builds a handoff, comments it back to the issue, and can mark it as claimed.
- GitHub Actions remains the validation and READY evidence surface. The queue workflow prepares a Codex handoff and explicitly triggers Codex with an `@codex` comment.

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

Training or benchmark data:
- `training-data:missing`

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

## Queue Workflow

Use GitHub Actions -> `Codex Issue Queue` to prepare the next issue handoff.

Automatic behavior:

- Scheduled queue runs execute every two hours by default.
- Set `CODEX_QUEUE_DISABLED=1` only when the queue must be temporarily stopped.
- Scheduled and post-merge runs claim the selected issue by default.
- Manual runs also claim by default unless `apply_claim=false` is selected.
- When a PR with `Closes #<issue>` is merged, the workflow marks the linked issue `agent:done`, removes active execution labels, and immediately tries to prepare the next ready issue.

Manual run inputs:

- `issue_number`: explicit issue number, or empty to select the oldest ready issue.
- `apply_claim`: add `agent:claimed` after a valid handoff is produced.
- `comment_handoff`: post the generated handoff back to the issue.

Optional repository variables:

```text
CODEX_QUEUE_DISABLED=1       # emergency stop; leave unset for automatic flow
CODEX_QUEUE_APPLY_CLAIM=true # optional explicit scheduled-claim setting; default is true
```

Detailed workflow guidance lives in [codex-issue-queue.md](codex-issue-queue.md).

## Training Data Gap Protocol

If a selected task discovers missing training, benchmark, corpus, fixture, or holdout data, the implementing agent must not silently block the whole queue.

The agent must post a separate issue comment starting with:

```text
TRAINING_DATA_GAP
```

The comment must include:

1. exact missing data or path,
2. required counts or classes,
3. current available counts or classes,
4. whether useful report-only work can still be completed,
5. the smallest next data-acquisition action.

Then the agent must add `training-data:missing`, remove `agent:claimed` if the issue cannot proceed, and leave unrelated ready issues for the next queue run.

## Issue to PR Flow

1. Create an issue with the KindleMaster task template.
2. Add `autopilot:allowed` only when the issue is complete enough to execute.
3. Run `python kindlemaster.py orchestrate sync --issues-json <file>` or use the `Codex Issue Queue` workflow.
4. The workflow claims a ready issue and posts an `@codex` handoff.
5. Codex executes the issue in one coherent PR with `Closes #<issue>`.
6. Codex runs the commands recommended by the issue gates.
7. Codex attaches test results, workflow artifacts, and residual risks to the PR.
8. READY enforcement blocks merge when evidence is missing.
9. After merge, the queue marks the issue `agent:done` and attempts the next ready issue.

## Guardrails

- Never commit directly to `main`.
- Do not execute issues missing acceptance criteria or validation.
- Keep one issue per branch and one branch per PR unless an epic explicitly says otherwise.
- For conversion-quality changes, prefer `workflow baseline/verify` evidence when a concrete fixture exists.
- Missing training/corpus data is a separate `TRAINING_DATA_GAP` signal, not a reason to stop unrelated automation.
- Do not put secrets, raw email addresses, tokens, or private document content into issues or PRs.
