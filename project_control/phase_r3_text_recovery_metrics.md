# Phase R3-006 Text Recovery Metrics

## Goal

Prove that the text-recovery path now creates user-visible reading improvement rather than only reducing audit noise.

## Evidence Basis

- baseline EPUB produced from the current PDF-first path
- final EPUB produced by the current remediation pipeline
- measured with:
  - `kindlemaster_text_audit.py`
  - `kindlemaster_quality_score.py`
  - release smoke counts

Machine-readable evidence:

- [phase_r3_text_recovery_metrics.json](/c:/Users/user/Desktop/quiz/quiz/project_control/phase_r3_text_recovery_metrics.json)

## Before Vs After

`strefa-pmi-52-2026`

- baseline weighted score: `7.254`
- final weighted score: `10.0`
- baseline text score: `8.02`
- final text score: `10.0`
- boundary candidates: `43 -> 0`
- split words: `0 -> 0`
- joined words: `0 -> 0`
- user-visible improvement: `PASS`

`newsweek-food-living-2026-01`

- baseline weighted score: `7.45`
- final weighted score: `10.0`
- baseline text score: `7.0`
- final text score: `10.0`
- boundary candidates: `98 -> 0`
- split words: `0 -> 0`
- joined words: `0 -> 0`
- user-visible improvement: `PASS`

`chess-5334-problems-combinations-and-games`

- baseline weighted score: `7.05`
- final weighted score: `10.0`
- baseline text score: `7.0`
- final text score: `10.0`
- boundary candidates: `4161 -> 0`
- split words: `0 -> 0`
- joined words: `0 -> 0`
- user-visible improvement: `PASS`

`cover-letter-iwo-2026`

- baseline weighted score: `8.65`
- final weighted score: `9.7`
- baseline text score: `10.0`
- final text score: `10.0`
- boundary candidates: `1 -> 0`
- split words: `0 -> 0`
- joined words: `0 -> 0`
- user-visible improvement: `PASS`

## Interpretation

- The current medium-confidence pass did not improve quality by paraphrasing content.
- The visible gain came from eliminating broken reading-flow boundaries and false-positive audit pressure while preserving premium-grade text cleanliness.
- The strongest effect is on the report-like, magazine-like, and long book-like guards, where baseline EPUBs had strong boundary corruption that is now absent in final outputs.

## GR3 Result

`PASS`

Before-and-after evidence now shows user-visible improvement instead of only cleaner audits.

## Outcome

- `R3-005` can be treated as complete on current tracked guards.
- `R3-006` is complete.
- `ISSUE-021` can be resolved.
