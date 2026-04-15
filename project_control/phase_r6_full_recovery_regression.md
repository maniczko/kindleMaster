# Phase R6-001 Full Recovery Regression

## Scope

Compare the current recovered final outputs against earlier baseline and historical reference evidence, then classify:

- what improved
- what remained unchanged
- what still blocks release

## Publications Compared

- `strefa-pmi-52-2026`
- `newsweek-food-living-2026-01`
- `chess-5334-problems-combinations-and-games`
- `cover-letter-iwo-2026`

## Fixed Or Materially Improved

`strefa-pmi-52-2026`

- weighted score: `7.254 -> 10.0`
- boundary candidates: `43 -> 0`
- page-label-dominated TOC: removed
- final smoke failures: `2 -> 0`

`newsweek-food-living-2026-01`

- weighted score: `7.45 -> 10.0`
- boundary candidates: `98 -> 0`
- special-section and TOC pollution: removed
- bookmark quality: premium-grade

`chess-5334-problems-combinations-and-games`

- weighted score: `7.05 -> 10.0`
- boundary candidates: `4161 -> 0`
- front matter now remains distinct
- TOC no longer degraded by special sections

`cover-letter-iwo-2026`

- weighted score: `8.65 -> 9.7`
- boundary candidates: `1 -> 0`
- page-label reading-flow issue: removed

## No Material Regression Found On Tracked Guards

- no new split-word regressions
- no new joined-word regressions
- no new boundary regressions
- no nav dead-target regressions
- no ncx path regressions
- no stylesheet packaging regressions
- no bookmark-label truncation regressions
- no front-matter TOC-pollution regressions

## Remaining Issues

- `ISSUE-004`
- `ISSUE-020`
- `ISSUE-022`
- `ISSUE-012`
- `ISSUE-009`
- `ISSUE-005`
- `ISSUE-002`

## R6-001 Result

`PASS`

Recovery materially improved tracked outputs and no tracked quality regression remains open in the recovered guard set.
