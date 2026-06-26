# Branch protection checklist for main

Configure this in GitHub repository settings or an organization ruleset.

Recommended settings:

- Require pull requests before merging.
- Require at least one approval.
- Require conversation resolution.
- Require status checks before merge.
- Require branches to be up to date.
- Required status check: ready-gate.
- Prevent direct destructive updates to main.

The ready-gate workflow aggregates governance, quick, and release readiness lanes. After enabling the rule, verify with a small test PR that GitHub blocks merge until ready-gate succeeds.
