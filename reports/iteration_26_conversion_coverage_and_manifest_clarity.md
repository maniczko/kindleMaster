# Iteration 26 - Conversion Coverage And Manifest Clarity

## Summary

This iteration hardened the runtime against a user-visible trust failure: the conversion flow looked too fast, did not prove full-file coverage, and exposed sample publication references in a misleading way. The fix focused on traceability, safer baseline conversion rules, and clearer UI separation between the current upload and repository benchmark state.

## Key Results

- Added explicit page-coverage proof to the end-to-end report and conversion response.
- Removed silent `publication_id` auto-fill from known sample filenames.
- Clarified in the UI that repository benchmark state is not the current upload.
- Made profile and OCR intent visible in the run output.
- Tightened hybrid conversion so it only applies when a page is both multi-column and image-bearing.
- Fixed a same-iteration semantic regression where `Strefa PMI w liczbach` reappeared as `h1`.

## Validation

- `31 passed` in `pytest`
- active release sample coverage: `84/84/84`, score `10.0/10`
- long book-like sample coverage: `1184/1184/1184`, score `8.65/10`
- Phase 12 release-gate enforcement still returns `FAIL`, but now only because pre-existing release blockers remain open

## Remaining Release Blockers

- `ISSUE-004`
- `ISSUE-020`
- `ISSUE-022`

## Next Step

Return to `FX-011` and `R3-005`. The repository now proves page coverage and conversion intent, so the next meaningful work is still deeper finalizer decomposition and medium-confidence text cleanup without paraphrase.
