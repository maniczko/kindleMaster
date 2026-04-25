# GitHub READY Enforcement

This document maps the local KindleMaster READY model onto lightweight GitHub-side enforcement.

## Required Workflow

The canonical GitHub Actions workflow for this repo is:

- `.github/workflows/ready-enforcement.yml`

It defines three stable checks:

- `ready-governance`
- `ready-quick`
- `ready-release`
- `ready-gate`

## Local To GitHub Mapping

Local READY lanes:

- `python kindlemaster.py test --suite quick`
- `python kindlemaster.py test --suite release`

GitHub mirrors them as:

- `ready-governance` -> developer bootstrap, Python matrix, static-quality, dependency, security, and coverage governance
- `ready-quick` -> `python kindlemaster.py test --suite quick`
- `ready-release` -> `python kindlemaster.py test --suite release`
- `ready-gate` -> aggregate branch-protection check that fails unless governance, quick, and release lanes pass

## Governance Gates

`ready-governance` keeps external enforcement lightweight while making quality drift visible before expensive conversion lanes run:

- Python compatibility: Python 3.12, 3.13, and 3.14 on Ubuntu, plus a Windows canary on Python 3.14.
- Static-quality: `ruff` runs correctness-focused rules only (`E9,F63,F7,F82`) over governance/control-plane files so legacy conversion style debt does not block unrelated work.
- Dependency consistency: `python -m pip check` runs on every matrix entry.
- Security audit: `pip-audit` runs once on the Ubuntu Python 3.14 lane against `requirements.txt` and `requirements-dev.txt` with a 60-second network timeout.
- Coverage threshold: governance/control-plane tests run through `coverage` with `GOVERNANCE_COVERAGE_FAIL_UNDER=75`.
- Core conversion coverage: selected conversion modules run through `coverage` once on Ubuntu Python 3.14 with `CORE_CONVERSION_COVERAGE_FAIL_UNDER=45`.
- Artifact upload: governance artifacts, quick READY evidence, and release READY evidence are uploaded through `actions/upload-artifact@v4`.

The core conversion coverage threshold is intentionally modest because the current corpus gate is still being stabilized. Raise it only after broader corpus blockers are green.

## Reference Inputs In Clean CI

`python kindlemaster.py prepare-reference-inputs` is safe to run in a clean GitHub checkout. Large source-backed samples under `example/` are intentionally not tracked, so the preparation step copies the real local sample when it exists and otherwise generates a deterministic surrogate fixture with the same manifest case id and document class.

The fallback is CI evidence only. Local release-quality work should still prefer the real `example/` source files when they are available on the operator machine.

## Branch Protection Recommendation

In GitHub branch protection for `main`, require:

- `ready-gate`

This keeps one stable external check name even if the underlying workflow evolves, while still preserving the stricter local split between quick and release lanes.

## Notes

- The repo can define workflow names and stable check names, but GitHub branch protection must still be configured in repository settings.
- Local runtime/browser/corpus details remain governed by `kindlemaster.py`, `AGENTS.md`, and `docs/toolchain-matrix.md`.
- GitHub Actions artifacts are derived evidence, not normative project truth; use them to inspect failing reports and outputs after CI runs.
