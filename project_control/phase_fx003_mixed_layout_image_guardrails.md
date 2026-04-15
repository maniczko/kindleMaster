# FX-003 Mixed-Layout Image Guardrails

## Result

Generic image-backed and page-like handling is now enforced by executable audits instead of only by historical notes.

## What Was Added

- `kindlemaster_image_layout_audit.py`
- smoke-gate counts for:
  - `image_only_file_count`
  - `page_like_file_count`
  - `image_only_toc_target_count`
  - `page_like_toc_target_count`
- smoke checks for:
  - `no_image_only_toc_targets`
  - `no_page_like_toc_targets`
- executable tests in `tests/test_image_layout_quality.py`
- manifest-corpus enforcement in `tests/test_scenario_manifest.py`

## Evidence

- all manifest-backed final EPUBs pass `image_layout_pass = true`
- all manifest-backed final EPUBs keep `nav_target_to_image_only_count = 0`
- all manifest-backed final EPUBs keep `nav_target_to_page_like_count = 0`
- mixed-layout guard `chess-5334-problems-combinations-and-games` remains premium while proving no page-like TOC leakage

## Verdict

`FX-003` is satisfied for the current supported-profile corpus.
