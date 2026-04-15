# FX-011 Stage Decomposition Pass 2

Date: 2026-04-13
Task: `FX-011`
Owner: `Lead / Orchestrator`
Supporting agents:
- `Goodall`: safe joined-word false-positive suppression guidance
- `Aquinas`: finalizer stage-proof implementation

## Goal

Strengthen the finalizer so navigation quality is not only implicitly preserved, but explicitly proven after the `semantic_recovery -> navigation_rebuild` handoff.

## What Changed

- `kindle_semantic_cleanup.py` now emits stronger navigation-stage proofs:
  - `semantic_toc_entry_count`
  - `semantic_top_toc_entry_count`
  - `nav_entry_count`
  - `toc_entry_count`
  - `nav_target_count`
  - `toc_target_count`
  - `nav_dead_target_count`
  - `toc_dead_target_count`
  - `navigation_survives_semantic_rebuild`
  - `stage_integrity_ok`
  - `nav_labels_sample`
  - `toc_labels_sample`
- Finalizer report root now includes:
  - `stage_sequence`
  - `stage_integrity`
  - `navigation_quality_proof`
- `kindlemaster_end_to_end.py` now surfaces the proof contract directly in the run report:
  - `finalizer_stage_sequence`
  - `finalizer_stage_integrity`
  - `finalizer_navigation_proof`
  - `finalizer_stage_sequence_valid`
  - `finalizer_navigation_stage_integrity_ok`
- Focused regression coverage was added in `tests/test_finalizer_stage_proofs.py`.

## Evidence

- Active release sample:
  - `finalizer_stage_sequence_valid = true`
  - `finalizer_navigation_stage_integrity_ok = true`
  - semantic top-level TOC entries: `6`
  - nav entries: `6`
  - ncx entries: `6`
  - dead nav targets: `0`
  - dead ncx targets: `0`
- Magazine guard:
  - semantic top-level TOC entries: `15`
  - nav entries: `15`
  - ncx entries: `15`
  - dead nav targets: `0`
  - dead ncx targets: `0`
- Book guard:
  - semantic top-level TOC entries: `4`
  - nav entries: `4`
  - ncx entries: `4`
  - dead nav targets: `0`
  - dead ncx targets: `0`

## Validation

- `python -m py_compile kindlemaster_text_audit.py kindle_semantic_cleanup.py kindlemaster_end_to_end.py kindlemaster_release_gate.py tests\test_text_quality_thresholds.py tests\test_finalizer_stage_proofs.py`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf --publication-id newsweek-food-living-2026-01`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book`
- `python -m pytest -q`

## Result

`FX-011` remains `IN_PROGRESS`.

This pass closes the observability gap around navigation survival and stage order, but it does not yet decompose the finalizer into independently persisted stage artifacts. The destructive-stage risk is now easier to measure, but not fully removed.

