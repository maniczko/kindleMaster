# Phase R1 Integrity Validation

Date: 2026-04-12
Owner: Lead / Orchestrator
Gate: `GR1`

## Implemented Recovery Fixes

- final navigation now keeps package-relative `xhtml/...` paths instead of dropping the directory prefix
- final XHTML, nav, and title pages now reference the packaged stylesheet path consistently
- `title.xhtml` is preserved instead of being rewritten into an empty shell
- the end-to-end runner now validates stylesheet targets, nav anchors, ncx targets, and title-page presence

## Evidence On The Active Sample

Compared artifacts:

- baseline: `kindlemaster_runtime/output/baseline_epub/strefa-pmi-52-2026.epub`
- final: `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`

Measured deltas after the R1 recovery pass:

- `toc_entry_count`: `84 -> 15`
- `noisy_toc_entry_count`: `84 -> 0`
- `dead_nav_targets`: `0 -> 0`
- `missing_css_refs`: `0 -> 0`
- `duplicate_toc_labels`: `0 -> 1`
- `split_word_count`: `3 -> 2`

Structural proof from the runner:

- `baseline_validation.pass = true`
- `final_validation.pass = true`
- `final_validation.missing_stylesheets = []`
- `final_validation.nav_dead_targets = []`
- `final_validation.ncx_dead_targets = []`
- `final_validation.title_page_empty = false`

## What Improved Materially

- the final EPUB no longer ships broken nav/toc paths
- the final EPUB no longer references a non-packaged stylesheet
- the title page is present and readable again
- TOC noise dropped sharply instead of expanding

## What Is Still Not Good Enough

- title detection is still too weak for magazine-like front matter and mixed-layout pages
- some residual TOC entries are still not premium-quality article titles
- text cleanup improved only slightly on visible split-word artifacts
- the finalizer is still a large combined stage even though a proof gate now catches key failures

## GR1 Verdict

`PASS`

Reason:
The artifact lifecycle is explicit, overwrite-prone path loss is removed, stylesheet packaging is consistent, and the final-output proof gate now verifies the actual shipped EPUB instead of only package presence.
