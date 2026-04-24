# GitHub READY Enforcement

This document maps the local KindleMaster READY model onto lightweight GitHub-side enforcement.

## Required Workflow

The canonical GitHub Actions workflow for this repo is:

- `.github/workflows/ready-enforcement.yml`

It defines three stable checks:

- `ready-quick`
- `ready-release`
- `ready-gate`

## Local To GitHub Mapping

Local READY lanes:

- `python kindlemaster.py test --suite quick`
- `python kindlemaster.py test --suite release`

GitHub mirrors them as:

- `ready-quick` -> `python kindlemaster.py test --suite quick`
- `ready-release` -> `python kindlemaster.py test --suite release`
- `ready-gate` -> aggregate branch-protection check that fails unless both READY lanes pass

## Branch Protection Recommendation

In GitHub branch protection for `main`, require:

- `ready-gate`

This keeps one stable external check name even if the underlying workflow evolves, while still preserving the stricter local split between quick and release lanes.

## Notes

- The repo can define workflow names and stable check names, but GitHub branch protection must still be configured in repository settings.
- Local runtime/browser/corpus details remain governed by `kindlemaster.py`, `AGENTS.md`, and `docs/toolchain-matrix.md`.
