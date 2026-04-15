# Phase R4-002 Front Matter Semantic Profiles

## Goal

Prove that front matter is semantically distinct, readable in linear flow, and no longer competes with article content on the tracked guard corpus.

## What Changed

- `kindle_semantic_cleanup.py` now emits explicit chapter-profile markup through `data-km-profile`, `km-profile-*`, and aligned `epub:type` values on generated body and section nodes.
- `kindlemaster_quality_score.py` now prefers explicit semantic profile markup before falling back to the generic front-hint boundary model.
- `tests/test_front_matter_quality.py` and `tests/test_finalizer_stage_proofs.py` now require explicit semantic-profile evidence on the active tracked release sample.

## Evidence

Tracked results after rerun:

- `strefa-pmi-52-2026`
  - `detection_mode`: `explicit_semantic_profile_markup`
  - `front_matter_file_count`: `2`
  - `first_content_file`: `EPUB/xhtml/page-0003.xhtml`
  - `article_heading_leaks`: `0`
  - `nav_pollution_count`: `0`
  - `distinctness_pass`: `true`

- `newsweek-food-living-2026-01`
  - `detection_mode`: `explicit_semantic_profile_markup`
  - `front_matter_file_count`: `2`
  - `first_content_file`: `EPUB/xhtml/page-0003.xhtml`
  - `article_heading_leaks`: `0`
  - `nav_pollution_count`: `0`
  - `distinctness_pass`: `true`

- `chess-5334-problems-combinations-and-games`
  - `detection_mode`: `leading_front_hint_then_content_boundary`
  - `front_matter_file_count`: `10`
  - `first_content_file`: `EPUB/xhtml/page-0011.xhtml`
  - `article_heading_leaks`: `0`
  - `nav_pollution_count`: `0`
  - `distinctness_pass`: `true`

- `cover-letter-iwo-2026`
  - `detection_mode`: `leading_front_hint_then_content_boundary`
  - `front_matter_file_count`: `0`
  - `first_content_file`: `EPUB/xhtml/page-0001.xhtml`
  - `article_heading_leaks`: `0`
  - `nav_pollution_count`: `0`
  - `distinctness_pass`: `true`

## GR4 Result

`PASS`

Front matter is now semantically distinct and readable on the tracked guard set.

## Remaining Uncertainty

- Long book-like and document-like guards still rely on the generic boundary fallback rather than explicit semantic profiles on every early page.
- Mixed-layout and image-heavy publications outside the tracked guard set still require broader evidence before this can be treated as universally complete.
