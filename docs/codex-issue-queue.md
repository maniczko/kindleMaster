# Codex issue queue contract

This repository uses GitHub Issues as the source of truth for agent-executable work.

## Executable issue contract

An issue may be executed only when all of these are true:

- The issue is open.
- It has `agent:ready`.
- It has `autopilot:allowed`.
- It has exactly the relevant `area:*` labels.
- It has acceptance criteria.
- It has validation commands.
- It does not have `agent:blocked`.
- It does not have `autopilot:requires-human`.
- It does not have `needs-product-decision`.

## Human gate

`autopilot:requires-human` is a hard stop. Do not start work, open a PR, or mark an issue done until a human decision is recorded and the label is removed.

## PR contract

A PR created from an issue should include:

- `Closes #<issue-number>` when it is intended to complete the issue.
- Summary.
- Changed files.
- Tests run.
- Known failures.
- Risks.
- Rollback notes.

If validation fails, the PR should be labeled `agent:needs-fix` or `agent:needs-review`, not merged.

## Queue behavior

The safe queue behavior is one issue at a time:

1. Select the oldest executable issue.
2. Claim or mark it in progress.
3. Create one branch for one issue.
4. Open one PR.
5. Wait for CI and review.
6. Merge only after checks pass or a human explicitly accepts residual risk.
7. Only then move to the next issue.

## Current limitation

The workflow implementation should enforce the contract above before executing code. If the workflow cannot verify the labels and required sections, it should stop and comment with blockers.
