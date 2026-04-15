# Iteration 18 - Recovery R2 Semantic Pass

Date: 2026-04-12
Verdict: `PARTIALLY RECOVERED`

## Completed in this iteration

- continued `R2-001` with a safer title-detection pass
- completed `FX-010` by constraining heading promotion and front-matter/banner leakage
- generated fresh end-to-end evidence on `samples/pdf/strefa-pmi-52-2026.pdf`

## What improved

- final TOC dropped from `84` entries to `5`
- noisy TOC entries dropped from `84` to `0`
- final EPUB stayed technically valid
- interview-style page `page-0050.xhtml` now opens with a real `h1` and paragraph-level byline
- page-level false `h1` promotion on `page-0007.xhtml` was reduced to `h2`

## What did not improve enough

- text cleanup is still only slightly better and remains tracked in `ISSUE-021`
- multi-page article continuity is still weak, so several real article starts remain under-detected
- the current semantic pass is cleaner, but still not premium-grade for mixed-layout magazine structure

## New recovery tracking

- kept `ISSUE-020` open as `IN_FIX`
- added `ISSUE-023` for page-level article continuity and under-selection risk
- added `LC-011` for low-confidence continuation pages
- added `FX-012` for continuation-aware article-opening recovery

## Next task

`R2-002` - Reconstruct valid article opening structure
