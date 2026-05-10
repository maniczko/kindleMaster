# VAT-206 Governance Dashboard

This document defines the governance dashboard emitted by:

```powershell
python kindlemaster.py status
```

The dashboard is generated into `reports/project_status.json` and `reports/project_status.md`. It is derived evidence, not a hand-maintained status board.

## Evidence Lanes

The VAT-206 dashboard tracks the latest available evidence for these lanes:

| Lane | Canonical command | Primary evidence |
| --- | --- | --- |
| `doctor` | `python kindlemaster.py doctor` | `reports/governance/doctor.json` |
| `quick` | `python kindlemaster.py test --suite quick` | `reports/governance/quick.json` |
| `corpus` | `python kindlemaster.py test --suite corpus` | `reports/corpus/corpus_gate.json` |
| `release` | `python kindlemaster.py test --suite release` | `reports/governance/release.json` |
| `ui_state_screenshots` | `python kindlemaster.py test --suite runtime` | `reports/ui-state-screenshots/latest/manifest.json` |
| `status` | `python kindlemaster.py status` | `reports/project_status.json` |

The `doctor` evidence includes an `agent_readiness` section covering repo-local Codex config, pinned Playwright MCP, enabled plugins, installed KindleMaster skills, `.githooks` configuration, and stale `.claude/settings.local.json` markers.

Every governance evidence artifact should include:
- `generated_at`
- `command`
- `status`
- `returncode`
- `elapsed_seconds`
- `notes`

If an evidence file is missing, the dashboard reports that lane as `unavailable` and keeps the expected artifact path visible. Missing lane evidence is a dashboard warning signal; it does not rewrite the authoritative command contract.

The `ui_state_screenshots` lane is a runtime UI evidence lane. Its manifest records state-based screenshots and horizontal-overflow checks for desktop, tablet, mobile, conversion outcomes, and library states. Any recorded horizontal overflow is treated as failed UI evidence.

## Evidence Freshness

Each evidence lane records:

- `updated_at` from the selected artifact,
- `freshness_status`,
- `age_hours`,
- `max_age_hours`,
- a lane-specific freshness warning when evidence is stale.

Evidence older than 168 hours is reported as `stale` and adds a warning to the generated project status. The `status` lane is refreshed by the current `python kindlemaster.py status` run.

## Workflow Completeness

The status report scans `reports/workflows/<run_id>/` and summarizes:

- complete workflow count,
- incomplete workflow count,
- baseline-only workflow count,
- latest completed workflow,
- latest incomplete workflow.

A complete workflow has baseline, isolation, verification, before/after, regression, and smoke report artifacts. A baseline-only workflow has baseline evidence but no verify evidence yet, so generated status warns that `workflow verify` is still required.

## Drift Checks

`python kindlemaster.py status` compares the command and governance mirrors across:

- `kindlemaster.py`
- `README.md`
- `.codex/config.toml`
- `.codex/README.md`
- `AGENTS.md`
- `docs/toolchain-matrix.md`

The checks verify that first-class commands, supported test-suite lanes, and project-status evidence paths stay aligned with the executable parser and governance source-of-truth rules.

## Active Session Overrides

`.codex/config.toml` stores repo-local defaults for future KindleMaster sessions and collaborators. An active session can still receive stricter or different runtime policy from the Codex harness, developer instructions, or workspace permissions.

When current session policy differs from `.codex/config.toml`, the current session policy wins for that run. Do not edit `.codex/config.toml` only to mirror a temporary session override. Update repo config only when the intended repo-local defaults themselves change.

In practical terms:

- active session permissions control what the agent may do right now,
- `.codex/config.toml` remains the repo-local defaults contract,
- generated status should document the distinction instead of treating it as policy drift.

## Local Hooks

Developer bootstrap installs the balanced local hook policy through:

```powershell
python scripts/install_git_hooks.py --install
```

The installer is skipped for `python kindlemaster.py bootstrap --runtime-only`, `CI=true`, or `KINDLEMASTER_SKIP_GIT_HOOKS=1`.

Hook expectations:
- `.githooks/pre-commit` runs static correctness checks and fast agent/governance tests.
- `.githooks/pre-push` runs `python kindlemaster.py test --suite quick` and `python kindlemaster.py status`.
