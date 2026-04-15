# R4-001 Front Matter Boundary Recovery Audit

Date: 2026-04-13
Task: `R4-001`
Owner: `Structure & Semantics`
Supporting agents:
- `Beauvoir`: front-matter distinctness root-cause analysis for the long book-like guard

## Goal

Audit why front matter and organizational material were still depressing quality scores on tracked guards, then verify whether the repository can distinguish true front matter from ordinary article flow generically rather than by publication-specific exceptions.

## Root Cause

- the previous audit logic treated almost every XHTML file before the first detected `h1` as front matter
- long book-like publications with delayed first article headings were over-classified as polluted front matter
- editorial front matter with real prose was incorrectly scored as article leakage instead of being recognized as legitimate pre-article reading material
- this made `tactits` look structurally worse than it really was even after navigation and text cleanup were already strong

## What Changed

- `kindlemaster_quality_score.py` now detects front matter via a generic leading-front-hint boundary model instead of a blanket “everything before first h1” rule
- the audit distinguishes:
  - structural front matter
  - editorial front matter
  - true article flow
- editorial front matter no longer counts as article-heading leakage or TOC pollution when it remains semantically distinct
- tracked guards now expose:
  - `detection_mode`
  - `first_content_file`
  - `editorial_front_matter_file_count`
  - `distinctness_pass`

## Evidence

### Active release sample

- `strefa-pmi-52-2026`
- front matter files: `4`
- editorial front matter files: `3`
- first content file: `EPUB/xhtml/page-0005.xhtml`
- distinctness: `PASS`
- weighted score: `10.0/10`

### Magazine guard

- `newsweek-food-living-2026-01`
- front matter files: `2`
- editorial front matter files: `1`
- distinctness: `PASS`
- weighted score: `10.0/10`

### Book-like guard

- `chess-5334-problems-combinations-and-games`
- front matter files: `10`
- editorial front matter files: `4`
- first content file: `EPUB/xhtml/page-0011.xhtml`
- distinctness: `PASS`
- weighted score: `10.0/10`

### Document-like fixture

- `cover-letter-iwo-2026`
- front matter files: `0`
- distinctness: `PASS`
- weighted score: `9.7/10`

## Validation

- `python -m py_compile kindlemaster_quality_score.py kindle_semantic_cleanup.py kindlemaster_end_to_end.py`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf --publication-id newsweek-food-living-2026-01`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/cover-letter-iwo-2026.pdf --publication-id cover-letter-iwo-2026`
- `python -m pytest -q`

## Result

`R4-001` is `DONE`.

The repository now has evidence that front matter and organizational sections can be identified generically on the tracked guard set without publication-specific word hacks. This closes the audit part of Phase R4, but it does not yet mean front-matter semantics are fully rebuilt for every mixed-layout publication, so `R4-002` remains necessary.
