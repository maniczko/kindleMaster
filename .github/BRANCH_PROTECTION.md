# Branch protection checklist for `main`

These settings cannot be enforced by repository files alone. Configure them in GitHub repository settings or an organization ruleset.

## Required rule

Target branch pattern:

```text
main
```

Recommended settings:

- Require a pull request before merging.
- Require approvals before merge. Use at least one approval for normal work.
- Require conversation resolution before merge.
- Require status checks to pass before merging.
- Require branches to be up to date before merging.
- Required status check: `ready-gate`.
- Block force pushes.
- Block branch deletions.
- Restrict bypass permissions to repository admins only, if bypass is needed at all.

## Why `ready-gate`

The workflow `.github/workflows/ready-enforcement.yml` keeps `ready-gate` as the stable external branch-protection check. It aggregates:

- `ready-governance`
- `ready-quick`
- `ready-release`

Requiring only `ready-gate` keeps branch protection stable while the underlying CI lanes can evolve.

## Manual verification

After changing this rule, open a small test PR and confirm that GitHub blocks merge until `ready-gate` succeeds.
