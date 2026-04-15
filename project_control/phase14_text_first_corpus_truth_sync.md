# Phase 14 Text-First Corpus Truth Sync

## Purpose

Synchronize the repository after the text-first hardening pass so that:
- corpus-wide quality-first proof is first-class evidence
- localhost and `quality-state` expose corpus truth instead of only one accepted artifact
- quality-loop state no longer points at archived tasks
- genericity and screenshot-fallback safeguards are executable and release-visible

## Changes

- Hardened corpus-wide release-gate reporting in `kindlemaster_release_gate_enforcer.py`
  - per-publication `what_is_good`
  - per-publication `what_is_bad`
  - fallback counts
  - pre-pytest provisional corpus report write to avoid stale-report bootstrap failures
- Hardened `kindlemaster_webapp.py`
  - `/quality-state` now exposes:
    - `repository_truth`
    - `corpus`
    - stronger loop-state status
  - accepted benchmark now prefers a passing release-eligible report over the most recent arbitrary report
  - `/convert` now exposes explicit text-first and fallback headers
- Hardened `kindlemaster_quality_loop.py`
  - quality-loop state now carries `state_kind`
  - quality-loop state now carries `pytest_suite_count`
- Added executable tests:
  - `tests/test_corpus_quality_state.py`
  - `tests/test_genericity_guards.py`

## Bootstrap Bug Found And Fixed

The new corpus-quality tests originally failed because the gate runner executed pytest before overwriting the old corpus report, so tests were reading a stale `FAIL` artifact from the previous attempt. The fix was to write a provisional corpus report before pytest and then overwrite it with the final verdict after pytest completes.

## Verification

- `python -m pytest -q` -> `57 passed`
- `python kindlemaster_release_gate_enforcer.py --corpus --quality-first` -> `PASS`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026` -> `PASS`

## Current Truth

- release readiness: `READY`
- corpus-wide quality-first gate: `PASS`
- unjustified page-image fallback on tracked corpus: `0`
- active release publication score: `10.0/10`
- next hardening task: `P14-004`

## Remaining Hardening Work

- add OCR-stressed/scanned fixture coverage
- add stronger multi-page document-like coverage
- widen release breadth beyond the single current release-eligible publication
