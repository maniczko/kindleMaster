# FX-011 Stage Decomposition Pass 3

## Goal

Reduce destructive finalizer risk by splitting the old semantic-recovery monolith into a pure planning stage and a separate writeback stage with observable artifacts.

## What Changed

- `kindle_semantic_cleanup.py` now splits the semantic path into:
  - `semantic_planning`
  - `semantic_apply`
- Planning computes:
  - processed chapter decisions
  - `toc_entries`
  - `solution_targets`
  without writing chapter XHTML back to disk.
- Apply consumes the stored plan and performs:
  - title and cover normalization
  - chapter XHTML writeback
  - problem-solution link injection
- Stage artifacts now include:
  - `semantic_plan.json`
  - `chapter_write_manifest.json`

## Proof

- finalizer stage sequence is now:
  - `extract`
  - `css_normalization`
  - `semantic_planning`
  - `semantic_apply`
  - `navigation_rebuild`
  - `metadata_normalization`
  - `packaging`
- `tests/test_finalizer_stage_proofs.py` now verifies:
  - semantic plan readiness
  - semantic plan consumption
  - chapter writes match the plan
  - plan/write artifacts are present in the manifest
- `tests/test_regressions.py` now blocks regressions in the new stage sequence

## Validation

- `python -m py_compile kindle_semantic_cleanup.py tests/test_finalizer_stage_proofs.py tests/test_regressions.py` -> `PASS`
- `python -m pytest -q` -> `48 passed`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026`
  - smoke: `PASS`
  - pytest gate: `48 passed`
  - quality score: `10.0/10`
  - final verdict: `FAIL` only because open high blockers remain

## Outcome

`FX-011` is still `IN_PROGRESS`, but the finalizer is now more observable and less monolithic than before.

## Remaining Gap

- The finalizer still runs through one module-level orchestration path.
- The next safe split is to make semantic planning and semantic apply independently reusable outside the main finalizer entrypoint.
