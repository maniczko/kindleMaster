# FX-011 Stage Decomposition Pass 1

Date: `2026-04-13`
Owner: `Lead / Orchestrator`
Task: `FX-011`

## Goal

Reduce the remaining monolith risk in the Kindle finalizer by moving stage execution into explicit workspace-backed functions instead of keeping all mutable execution inside one broad function body.

## Changes Applied

- Added explicit `FinalizerWorkspace` state object in [kindle_semantic_cleanup.py](/c:/Users/user/Desktop/quiz/quiz/kindle_semantic_cleanup.py).
- Split the runtime path of `finalize_epub_for_kindle_detailed()` into stage runners:
  - `_create_finalizer_workspace`
  - `_run_css_normalization_stage`
  - `_run_semantic_recovery_stage`
  - `_run_navigation_rebuild_stage`
  - `_run_metadata_normalization_stage`
  - `_run_packaging_stage`
- Preserved the external report contract:
  - `extract`
  - `css_normalization`
  - `semantic_recovery`
  - `navigation_rebuild`
  - `metadata_normalization`
  - `packaging`
- Isolated test end-to-end runs from shared runtime output in [tests/conftest.py](/c:/Users/user/Desktop/quiz/quiz/tests/conftest.py) by switching the active release fixture to per-session temp directories.

## Why This Matters

Before this pass:
- the finalizer reported stages, but stage execution still lived in one large function body
- Stage 12 pytest could fail spuriously on Windows because test fixtures reused shared runtime artifact paths

After this pass:
- stage execution is explicit and workspace-backed
- stage boundaries are easier to reason about and extend
- tests no longer collide with shared baseline/final EPUB paths during local runs

## Validation

- `python -m py_compile kindle_semantic_cleanup.py kindlemaster_end_to_end.py kindlemaster_quality_score.py kindlemaster_release_gate_enforcer.py`
  - `PASS`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
  - `PASS`
- `python -m pytest -q`
  - `29 passed`

## Gate Interpretation

`FX-011` is still `IN_PROGRESS`.

What is now true:
- finalizer stages are no longer only cosmetic report labels
- test and local-runtime overwrite pressure is lower
- finalizer proof remains stable on the active release sample

What still remains open:
- cleanup, semantics, navigation, and packaging still share one module and one high-level orchestration entrypoint
- stage-specific acceptance and promotion logic still depends on broader repository gates rather than per-stage persisted artifacts
- mixed-layout and image-heavy risk still remains outside this pass
