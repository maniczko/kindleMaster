# Workflow

## Autonomous State Machine

Task lifecycle:
- `TODO -> IN_PROGRESS -> DONE`
- `TODO -> IN_PROGRESS -> REVIEW`
- `TODO -> IN_PROGRESS -> BLOCKED`
- `TODO -> DEFERRED`

Issue lifecycle:
- `OPEN -> IN_FIX -> RESOLVED`
- `OPEN -> NEEDS_REVIEW`
- `OPEN -> REGRESSION`
- `OPEN -> WONT_FIX`

## Mandatory Post-Task Update Order

After every task that changes state or findings, update in this order:
1. `project_control/backlog.yaml`
2. `project_control/issue_register.yaml`
3. `project_control/metrics.json`
4. `project_control/low_confidence_review_queue.yaml`
5. `project_control/status_board.md`
6. current iteration report in `reports/`

## Autonomous Loop

1. Read `project_control/backlog.yaml`
2. Read `project_control/input_registry.yaml`
3. Pick the highest-priority eligible `TODO` task
4. Move it to `IN_PROGRESS`
5. Execute the task
6. Update the control files in mandatory order
7. Run the assigned gate
8. On pass, move to `DONE`
9. On fail, move to `REVIEW` or `BLOCKED`, update issues, create `FX-*` if high or critical
10. Continue automatically
11. If verdict is `NOT READY`, generate continuation tasks and continue

## Stop Conditions

Only real blockers may stop execution:
- missing required input
- critical unresolved ambiguity that cannot be resolved safely
- unresolved release-blocking coupling
- missing mandatory scenario coverage that cannot be satisfied from current repo-local inputs
- a human decision is required for high-impact editorial ambiguity
- release verdict is `READY`

## Continue Conditions

If `NOT READY`:
- do not stop
- create continuation tasks
- keep release blockers explicit
- continue with the next eligible task automatically

## No Hidden Reset

- No stage may silently regenerate final artifacts from older sources.
- No earlier stage may overwrite a corrected final artifact.
- No stage may write into `kindlemaster_runtime/output/release_candidate/` unless the release gate already passed.
- No release claim may be based on an intermediate artifact when the final artifact differs.
- Destructive finalizer behavior is a tracked defect, not an accepted workflow shortcut.
- No stage may silently reintroduce screenshot or page-image fallback on a page that has already been recovered as text-first without explicit evidence and classification.

## Isolation Rule

`KINDLE MASTER` and `foreign frontend/runtime` must not share:
- runtime assumptions
- output paths
- build scripts
- conversion scripts
- CI workflows
- caches
- package or workspace assumptions
- environment assumptions
- release process assumptions

## Evidence Rule

No quality-affecting task may be closed without:
- artifact output
- issue updates
- metrics where relevant
- gate pass
- evidence
- regression check
- genericity preserved across the guard corpus when runtime behavior changed
