# Iteration 24 - Magazine Guard Recovery

Date: `2026-04-12`

## Scope

Continue remediation after the document-like fixture pass by closing `FX-012` on the tracked magazine-like guard publication.

## Work Performed

- tightened front-matter, promo, and TOC-signal classification in `kindle_semantic_cleanup.py`
- added merged-opening recovery for `title + subtitle + lead` paragraphs
- blocked interview-question and quote-like false titles from becoming `h1` or TOC entries
- regenerated the `newsweek-food-living-2026-01` final EPUB
- raised scenario-test expectations for the magazine-like fixture
- reran full pytest and release-gate enforcement

## Results

Magazine guard publication:
- source PDF: `samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf`
- final EPUB: `kindlemaster_runtime/output/final_epub/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.epub`
- quality score:
  - before: `6.93/10`
  - after: `10.0/10`

Quality deltas:
- `h1_count`: `1 -> 16`
- `toc_entry_count`: `1 -> 14`
- `duplicate_low_value_entries`: `0 -> 0`
- `front_matter.distinctness_pass`: `false -> true`
- `split_word_count`: `0 -> 0`
- `joined_word_boundary_count`: `2 -> 2`

Tests:
- `python -m pytest -q`: `29 passed`

Release gate recheck for active release sample:
- verdict: `FAIL`
- reason: unresolved high-severity repository issues still present
- remaining high blockers after this pass:
  - `ISSUE-004`
  - `ISSUE-020`
  - `ISSUE-022`

## Task State Changes

- `FX-012`: `DONE`
- `ISSUE-023`: `RESOLVED`

## Honest Outcome

This pass materially improved the repo-wide guard set and closed the specific magazine page-split recovery task. The repository still cannot be marked `READY`, because mixed-layout quality and finalizer decomposition remain open, and Phase 13 release evidence is still incomplete.
