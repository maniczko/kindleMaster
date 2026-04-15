# Iteration 17: Recovery R1 Integrity Fixes

Date: 2026-04-12
Mode: `PDF_AND_EPUB`
Owner: Lead / Orchestrator

## Scope

Executed:

- `R1-001`
- `R1-002`
- `R1-003`
- `FX-008`
- `FX-009`

Opened next:

- `R2-001`

## What Changed

- documented the baseline -> working tree -> final EPUB lifecycle
- enforced non-overwrite rules in workflow/orchestration
- repaired final navigation href generation
- repaired final stylesheet lifecycle
- preserved `title.xhtml`
- added a proof gate in the end-to-end runner for CSS, nav, ncx, and title-page integrity

## Validation Summary

Active sample:

- `samples/pdf/strefa-pmi-52-2026.pdf`

Result:

- baseline validation: `PASS`
- final validation: `PASS`

Measured improvement:

- TOC entries `84 -> 15`
- noisy TOC entries `84 -> 0`
- split-word artifacts `3 -> 2`

## Remaining Recovery Risks

- `ISSUE-020`: semantic/title quality is improved but still not premium
- `ISSUE-021`: text cleanup is still underpowered
- `ISSUE-022`: the finalizer is safer, but still too monolithic

## Next Task

`R2-001` - Rebuild title detection model for true article titles
