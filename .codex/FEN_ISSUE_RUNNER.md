# FEN Issue Runner

This file is the execution contract for Codex/agent work on the FEN P0 chain.

Epic: #18

## Core policy

```text
AI readout != placement accepted != full strict FEN accepted
```

Never:

- count AI-only readout as strict full FEN accepted,
- count `FEN_PLACEMENT_MACHINE_ACCEPTED` as full strict FEN accepted,
- weaken strict full FEN acceptance only to improve a metric,
- work on more than one issue in one branch,
- open one PR for multiple issues unless explicitly instructed,
- continue after BLOCKED or failed tests.

## Execution order

The approved FEN P0 execution order is:

1. #28 — centralize blocker categories
2. #19 — build strict regression diff: best 223 vs latest 180
3. #30 — add best-known strict baseline fixtures
4. #20 — add strict no-regression gate
5. #22 — build review diagnostics for 197 non-strict cases
6. #24 — audit and lock exact label lookup
7. #21 — separate strict / placement / AI metrics
8. #29 — add final strict readiness report
9. #23 — recover the 43 lost strict accepted cases
10. #25 — build AI consensus promotion queue
11. #26 — build AI tie-break square-level queue
12. #27 — separate AI unreadable / best-effort hard cases

Reason for this order:

- #28 and #30 provide shared primitives for #19/#20/#22/#29.
- #23 must not start until diagnostics and label lookup evidence exist.
- AI queues (#25/#26/#27) should start after strict regression control is in place.

## Required labels

An issue can be executed only when it has:

```text
agent:ready
autopilot:allowed
area:fen
```

Recommended lifecycle labels:

```text
agent:queued
agent:in-progress
agent:blocked
agent:ready-review
agent:done
agent:needs-artifact
```

If lifecycle labels do not exist in GitHub yet, use issue comments with the same status names.

## One-issue execution protocol

For each issue:

1. Read the issue body and this runner file.
2. Confirm the required labels are present.
3. Confirm all predecessor issues in the execution order are DONE or explicitly marked as resolved.
4. Create a dedicated branch.
5. Add an issue comment:

```markdown
## Codex status: IN_PROGRESS

Branch:
`<branch-name>`

Scope:
- only this issue
- no unrelated changes
- no weakening strict FEN acceptance
- no AI-only strict promotion
- no placement-only strict promotion
```

6. Implement only the issue scope.
7. Add or update tests required by the issue.
8. Run issue-specific tests.
9. Run relevant existing FEN tests when shared FEN logic changes.
10. Commit changes.
11. Open one PR.
12. PR description must use `.github/pull_request_template.md`.
13. Comment on the issue with DONE or BLOCKED.
14. Update #18 with progress.
15. Stop if BLOCKED, tests fail, PR creation fails, or required artifacts are missing.
16. Continue to the next issue only if the current issue is DONE and a PR exists.

## Branch naming

```text
#28 -> fen-p0-10-blocker-categories
#19 -> fen-p0-01-strict-regression-diff
#30 -> fen-p0-12-baseline-fixtures
#20 -> fen-p0-02-strict-regression-gate
#22 -> fen-p0-04-review-diagnostics
#24 -> fen-p0-06-exact-label-lookup
#21 -> fen-p0-03-metrics-separation
#29 -> fen-p0-11-strict-readiness
#23 -> fen-p0-05-recover-lost-strict
#25 -> fen-p0-07-ai-consensus-queue
#26 -> fen-p0-08-ai-tiebreak-queue
#27 -> fen-p0-09-hard-cases
```

## Required DONE comment

```markdown
## Codex status: DONE

Completed issue:
#<issue-number>

Branch:
`<branch-name>`

PR:
#<pr-number>

Summary:
- ...

Files changed:
- ...

Tests run:
```bash
...
```

Result:
- PASS

Artifacts:
- ...

Risks:
- ...

Next issue:
#<next-issue-number>
```

## Required BLOCKED comment

```markdown
## Codex status: BLOCKED

Issue:
#<issue-number>

Reason:
- ...

Missing artifact / failing command / policy conflict:
- ...

Tests run:
```bash
...
```

Next action required from human:
- ...

No next issue started.
```

## Tracker file

Codex should create or update:

```text
FEN_ISSUE_EXECUTION_LOG.md
```

Expected table:

| Issue | Status | Branch | PR | Tests | Result | Notes |
|---|---|---|---|---|---|---|

Status values:

- TODO
- IN_PROGRESS
- DONE
- BLOCKED
- SKIPPED

## Stop conditions

Stop immediately if:

- the current issue is BLOCKED,
- tests fail,
- required report artifacts are missing,
- PR creation fails,
- branch has unrelated changes,
- a requested implementation would weaken strict FEN policy,
- the task requires direct AI-to-strict promotion,
- a merge conflict requires human decision.

## Start command for Codex

```text
Read .codex/FEN_ISSUE_RUNNER.md. Start with the first issue in the execution order that is not DONE or BLOCKED. Work only on that issue, create one PR, update the issue and epic, then continue only if all stop conditions are clear.
```
