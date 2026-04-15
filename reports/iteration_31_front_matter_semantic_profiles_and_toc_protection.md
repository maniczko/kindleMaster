# Iteration 31 - Front Matter Semantic Profiles And TOC Protection

## Scope

This iteration completed `R4-002` and `R4-003`.

## Implemented

- Added explicit semantic profile markup for generated front matter, TOC, back matter, and article sections in the finalizer output path.
- Updated the quality scorer to prefer explicit semantic profiles before falling back to heuristic front-matter detection.
- Hardened release-gate counts with `front_matter_target_count`.
- Added pytest checks that require:
  - explicit semantic-profile evidence on the active release sample
  - zero front-matter TOC targets
  - semantic stage proofs with chapter-profile counts

## Evidence

- `python -m py_compile ...` -> `PASS`
- `python -m pytest -q` -> `47 passed`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026`
  - smoke: `PASS`
  - pytest gate: `47 passed`
  - quality score: `10.0/10`
  - final verdict: `FAIL` only because open high blockers still remain

Tracked guard front-matter result:

- `strefa-pmi-52-2026`
  - explicit semantic profiles
  - `front_matter_file_count = 2`
  - `nav_pollution_count = 0`

- `newsweek-food-living-2026-01`
  - explicit semantic profiles
  - `front_matter_file_count = 2`
  - `nav_pollution_count = 0`

- `chess-5334-problems-combinations-and-games`
  - generic fallback boundary still used
  - `front_matter_file_count = 10`
  - `nav_pollution_count = 0`

- `cover-letter-iwo-2026`
  - generic fallback boundary
  - `front_matter_file_count = 0`
  - `nav_pollution_count = 0`

Tracked guard TOC protection result:

- all tracked guards now have:
  - `front_matter_target_count = 0`
  - `special_section_toc_count = 0`
  - `suspicious_nav_label_count = 0`

## Quality Outcome

- `strefa-pmi-52-2026` -> `10.0/10`
- `newsweek-food-living-2026-01` -> `10.0/10`
- `chess-5334-problems-combinations-and-games` -> `10.0/10`
- `cover-letter-iwo-2026` -> `9.7/10`

## Task State

- `R4-002` -> `DONE`
- `R4-003` -> `DONE`
- `FX-011` -> still `IN_PROGRESS`
- `R3-005` -> still `IN_PROGRESS`

## Remaining Blockers

- `ISSUE-004`
- `ISSUE-020`
- `ISSUE-021`
- `ISSUE-022`

## Next Step

`R3-006` - recompute text metrics and prove visible medium-confidence reading-quality improvement, then continue deeper `FX-011` decomposition.
