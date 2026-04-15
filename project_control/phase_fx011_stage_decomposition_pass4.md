# FX-011 Stage Decomposition Pass 4

## Goal

Resolve the last release blocker by removing the remaining "one opaque entrypoint" risk from the finalizer.

## What Changed

- `kindle_semantic_cleanup.py` now exposes a reusable `FinalizerPipeline`.
- Each stage now has:
  - explicit dependencies
  - explicit acceptance checks
  - explicit acceptance status persisted in stage artifacts
- Stages are independently executable through `pipeline.run_stage(...)`.
- End-to-end reporting now surfaces:
  - `finalizer_acceptance_boundaries_ok`
  - per-stage `accepted`
  - per-stage `acceptance_checks`

## Acceptance Boundaries

- `extract`
  - `container_present`
  - `opf_present`
  - `mimetype_present`
- `css_normalization`
  - `css_exists`
  - `css_manifest_ref_present`
- `semantic_planning`
  - `semantic_plan_ready`
  - `heading_count`
  - `toc_entries_present`
- `semantic_apply`
  - `semantic_plan_consumed`
  - `chapter_writes_match_plan`
  - `title_page_non_empty`
  - `cover_page_state_valid`
  - `toc_entries_present`
- `navigation_rebuild`
  - `nav_exists`
  - `toc_exists`
  - `navigation_survives_semantic_rebuild`
  - `stage_integrity_ok`
- `metadata_normalization`
  - `title_matches`
  - `creator_matches`
  - `language_matches`
- `packaging`
  - `title_page_non_empty`
  - `nav_exists`
  - `ncx_exists`

## Proof

- `tests/test_finalizer_stage_proofs.py` now verifies:
  - independent stage execution
  - dependency enforcement
  - acceptance-boundary exposure in end-to-end reports
- `tests/test_regressions.py` now blocks regressions in `acceptance_boundaries_ok`
- fresh release-mode run for `strefa-pmi-52-2026` shows:
  - `finalizer_final_pass = true`
  - `finalizer_acceptance_boundaries_ok = true`
  - `stage_sequence_valid = true`

## Validation

- `python -m py_compile kindle_semantic_cleanup.py kindlemaster_end_to_end.py tests/test_finalizer_stage_proofs.py tests/test_regressions.py` -> `PASS`
- `python -m pytest -q` -> `53 passed`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026` -> `PASS`

## Outcome

`FX-011` is resolved. The finalizer is now decomposed enough that the remaining architectural release blocker is closed by executable evidence rather than narrative assurance.
