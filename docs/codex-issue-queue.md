# Codex Issue Queue

This runbook describes the repository-level queue workflow for one-issue-at-a-time Codex handoffs.

Workflow file:

```text
.github/workflows/codex-issue-queue.yml
```

## What the workflow does

The workflow is intentionally serialized with GitHub Actions concurrency. One run selects at most one issue, validates its KindleMaster task contract, builds a Codex handoff payload, comments the handoff back to the issue, and optionally marks the issue as claimed.

It uses the existing local governance command surface:

```powershell
python kindlemaster.py orchestrate doctor
python kindlemaster.py orchestrate sync --issues-json reports/github/issues.json
python kindlemaster.py orchestrate claim --issues-json reports/github/issues.json --issue-number <issue_number>
python kindlemaster.py orchestrate execute --issues-json reports/github/issues.json --issue-number <issue_number>
python kindlemaster.py orchestrate report --issues-json reports/github/issues.json --issue-number <issue_number>
```

## Issue selection rule

Automatic selection uses the oldest open issue matching all positive labels and none of the blocking labels.

Required labels:

- `agent:ready`
- `autopilot:allowed`

Excluded labels:

- `agent:claimed`
- `agent:blocked`
- `autopilot:requires-human`
- `needs-product-decision`

The issue must also pass the local KindleMaster task contract: required sections, one `area:*` label, and no blocking state.

## Manual run

Use GitHub Actions -> `Codex Issue Queue` -> `Run workflow`.

Inputs:

- `issue_number`: optional explicit issue number. Empty means select the oldest ready issue.
- `apply_claim`: when true, add `agent:claimed` after a valid handoff is produced.
- `comment_handoff`: when true, comment the generated handoff back to the issue.

## Scheduled run

The workflow has a two-hour schedule, but scheduled execution is gated by a repository variable.

To enable scheduled queue preparation, set repository variable:

```text
CODEX_QUEUE_ENABLED=1
```

To let scheduled runs mark selected issues as claimed, set:

```text
CODEX_QUEUE_APPLY_CLAIM=true
```

Leave `CODEX_QUEUE_APPLY_CLAIM` unset or false if you only want scheduled dry handoffs.

## Current execution boundary

This workflow prepares the queue and handoff. It does not modify source code by itself. That boundary is deliberate until an approved Codex executor is connected through an official or internally reviewed integration.

Safe operating model:

1. Queue workflow selects and validates one issue.
2. Queue workflow posts the handoff and recommended validation commands.
3. An approved Codex executor uses the handoff to create one branch and one PR.
4. READY enforcement validates the PR.
5. Human review merges after `ready-gate` passes.

## Label lifecycle

Recommended issue state transitions:

```text
agent:ready + autopilot:allowed
  -> agent:claimed
  -> agent:needs-review
  -> agent:done
```

Use `agent:blocked` when the issue contract is incomplete or execution discovers a decision that cannot be made safely by the agent.

## Guardrails

- Keep one issue per branch and one branch per PR.
- Do not put private document content, tokens, raw credentials, or personal email addresses into issues.
- Use `autopilot:requires-human` for tasks needing product decisions, platform setup, or access outside the repository.
- Keep branch protection on `main` with `ready-gate` required.
