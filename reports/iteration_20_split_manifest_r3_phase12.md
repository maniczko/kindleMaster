# Iteration 20: Split, Manifest, R3, and Phase 12 Foundation

Date: `2026-04-12`

## Scope

This iteration completed four connected workstreams:

1. physically split `foreign frontend/runtime` out of the current repository root
2. introduced a manifest-backed publication model for release metadata
3. repaired and re-measured `R3` text-audit logic
4. created the first executable Phase 12 release suites and wired them into CI

## What Changed

### Repository boundary

- extracted `foreign frontend/runtime` product assets into `an external repository path outside this workspace`
- removed foreign root runtime assumptions from the Kindle Master repository
- updated boundary and governance docs to reflect the physical split

### Manifest-backed metadata

- added `project_control/publication_manifest.yaml`
- wired `publication_id` and `release_mode` into runtime and localhost flows
- active release sample now uses trusted manifest metadata instead of slug-like or `Unknown` fallbacks

### Recovery R3

- replaced the noisy joined-word scan with a stricter token-level audit
- replaced the broad boundary scan with a narrower adjacency-based review scan
- extended deterministic hyphen-split repair so title-case + lowercase artifacts like `Syste- mowa` are fixed safely
- kept `Sylwester- Kanofocka` and brand/proper-name tokens in review-only status

### Phase 12 executable test suites

Added and passed:
- `tests/test_release_metadata.py`
- `tests/test_isolation_boundaries.py`
- `tests/test_scenario_manifest.py`
- `tests/test_text_quality_thresholds.py`
- `tests/test_navigation_quality.py`

Also added:
- `kindlemaster_release_gate.py`
- `kindlemaster_text_audit.py`

## Evidence

Release smoke on the active release-eligible sample now passes with:
- title: `Strefa PMI nr 52 (marzec 2026)`
- creator: `Project Management Institute Poland Chapter`
- failed checks: `[]`

Pytest status:
- `13 passed`
- `0 failed`

R3 signal quality improved materially:
- split-word matches: `2 -> 1`
- joined-word candidates: `216 -> 9`
- boundary candidates: `51 -> 7`

## Resolved In This Iteration

- `ISSUE-013`
- `ISSUE-024`
- `ISSUE-026`
- `FX-013`

## Still Blocking Release

- `FX-014`: no real document-like fixture yet
- `ISSUE-020` / `ISSUE-023`: continuation-aware article-opening recovery still incomplete
- `ISSUE-022`: finalizer decomposition and release-candidate enforcement still incomplete
- Phase 12 still lacks front-matter, typography/UX, broader regression, and full enforcement suites

## Next Eligible Tasks

1. `T12-005` add front matter and special-section release tests
2. `T12-006` add typography and UX baseline tests
3. `T12-008` add regression tests for known failure patterns
4. `T12-012` add release-candidate immutability and copy-up enforcement
5. `R3-005` apply controlled AI-assisted cleanup for medium-confidence cases

