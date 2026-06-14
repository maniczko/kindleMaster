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
- `ready-quick` -> `python kindlemaster.py test --suite quick`, Sprint 1 QA regression tests, and the optional Node contract hook
- `ready-release` -> `python kindlemaster.py test --suite release` with `KINDLEMASTER_RELEASE_PROOF_PROFILE=ci`
- `ready-gate` -> aggregate branch-protection check that fails unless governance, quick, and release lanes pass

## Sprint 1 QA Gate

`ready-quick` also runs:

```powershell
python -m unittest test_sprint1_quality_gates.py
```

This keeps release-score blockers visible for bad TOC quality, ad/sponsored fragments, AI notes leaked into reader text, and OCR artifacts without widening the bounded release lane.

## CI Release Proof Profile

GitHub Actions runs the release lane with `KINDLEMASTER_RELEASE_PROOF_PROFILE=ci`. This keeps the external READY gate deterministic on clean runners that do not have the full local premium PDF/OCR toolchain. The CI profile still runs release units, corpus units, standard smoke evidence, and one dense/report premium conversion case, but it does not claim the full local standard corpus proof. Local release-candidate work should continue to use the default `standard` profile.

## Chess FEN Corpus Gate

The local `standard` and `full` corpus proof profiles use the release-grade chess FEN corpus gate by default:

```powershell
python kindlemaster.py corpus --proof-profile standard
```

That route requires at least two real scanned chess FEN profiles, at least 20 manually verified seed labels per profile, exact FEN accuracy at or above the configured threshold, and `false_positive_count == 0`. Use an explicit override only for bounded diagnostics, not for release claims:

```powershell
python kindlemaster.py corpus --proof-profile standard --fen-min-profile-count 1
```

The CI profile remains intentionally bounded at one FEN profile because clean GitHub runners may lack the full local OCR/PDF corpus. A green CI release lane is therefore CI evidence, not proof of release-grade chess FEN generalization.

## Sprint 2 Runtime/Storage Gate

The quick suite now includes contract tests for the local-first runtime job
adapter and artifact storage abstraction. Those tests protect retry/timeout
metadata, replay context, local artifact fallback, R2/S3-compatible
configuration detection, retention defaults, and signed URL metadata without
requiring live Trigger.dev or R2 credentials in CI.

Browser-level Sprint 2 coverage lives in the runtime lane:

```powershell
python -m unittest test_sprint2_playwright_smoke.py
```

That Playwright smoke is registered in `python kindlemaster.py test --suite
runtime`, not in `ready-quick`, so the daily quality gate remains bounded.

## Sprint 3 AI Quality Gate

The quick suite includes offline AI quality intelligence contracts:

```powershell
python -m unittest test_ai_quality_intelligence.py test_ai_ocr_cleanup.py test_ai_toc_detection.py
```

These tests keep AI OCR cleanup scoped to suspicious fragments, AI TOC detection
scoped to low-confidence deterministic TOC, and provider failure or low
confidence on the deterministic fallback path. No live AI credentials are
required for READY.

## Node Contract Hook

This repo includes a minimal root Node workspace for static UI/API contract regressions. CI installs it only when `package.json` exists, then runs the canonical contract hook:

```powershell
npm run test:contracts:regression
```

The package also exposes `npm run test:js` and `npm run test:contract` for local use. If the workspace later migrates to pnpm for Sprint 2+ frontend work, keep the equivalent `pnpm run test:contracts:regression` hook so the READY workflow remains stable.

The Vitest coverage stays contract-focused: it protects static quality-state normalization and the Sprint 4 React shell without making browser automation part of the quick READY lane.

## Sprint 4 React UI Gate

Sprint 4 adds a React/Vite shell at `/app`; `python kindlemaster.py serve`
auto-builds it when needed, and `/` redirects to that shell. Clean checkouts
without `static/react/` must fail clearly or build the shell, not fall back to
the legacy panel. Local UI checks are:

```powershell
npm run build:ui
npm run test:ui
```

`npm run test:contracts:regression` now runs the static quality-state contract
tests plus the React UI contract tests. Browser/runtime coverage remains in
`python kindlemaster.py test --suite runtime`.

## Governance Gates

`ready-governance` keeps external enforcement lightweight while making quality drift visible before expensive conversion lanes run:

- Python compatibility: Python 3.12, 3.13, and 3.14 on Ubuntu, plus a Windows canary on Python 3.14.
- Static-quality: `ruff` runs correctness-focused rules only (`E9,F63,F7,F82`) over governance/control-plane files so legacy conversion style debt does not block unrelated work.
- Agent config contracts: `test_agent_config_contracts.py` keeps Codex config, local hooks, Claude examples, and agent readiness checks aligned.
- Dependency consistency: `python -m pip check` runs on every matrix entry.
- Security audit: `pip-audit` runs once on the Ubuntu Python 3.14 lane against `requirements.txt` and `requirements-dev.txt` with a 60-second network timeout.
- Coverage threshold: deterministic governance/control-plane paths (`kindlemaster.py` and `scripts/generate_project_status.py`) run through `coverage` with `GOVERNANCE_COVERAGE_FAIL_UNDER=75`.
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
