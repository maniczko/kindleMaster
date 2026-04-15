# Iteration 29

## Summary

This iteration focused on generic conversion fidelity and run visibility rather than publication-specific wording fixes.

- recovered inline ordered lists from PDF into real EPUB ordered lists
- added quality gating for flattened ordered lists
- added mandatory premium assessment after every run
- normalized visible versioning to `x.y`
- synced release-gate and quality-loop suites with the stronger checks

## Files changed

- `kindlemaster_structured_lists.py`
- `kindlemaster_versioning.py`
- `kindlemaster_pdf_to_epub.py`
- `kindlemaster_release_gate.py`
- `kindlemaster_quality_score.py`
- `kindlemaster_end_to_end.py`
- `kindlemaster_webapp.py`
- `kindlemaster_release_gate_enforcer.py`
- `kindlemaster_quality_loop.py`
- `kindlemaster_release_candidate.py`
- `tests/test_pdf_list_normalization.py`
- `tests/test_conversion_traceability.py`
- `tests/test_regressions.py`
- `VERSION`

## Validation

- `python -m py_compile ...` -> `PASS`
- `python -m pytest -q` -> `44 passed`
- active release gate -> `FAIL` only because open high-severity blockers remain

## Publication scores after this pass

- `strefa-pmi-52-2026` -> `10.0/10`
- `newsweek-food-living-2026-01` -> `10.0/10`
- `chess-5334-problems-combinations-and-games` -> `8.65/10`

## Notes

- no publication-specific string hacks were introduced
- list recovery is based on generic sequential marker detection
- every end-to-end run now emits machine-readable and human-readable premium evidence
