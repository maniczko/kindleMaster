# Phase R4-003 Special Section TOC Protection

## Goal

Prove that front matter and special sections no longer pollute Kindle navigation or degrade the main article flow.

## What Changed

- `kindlemaster_release_gate.py` now measures `front_matter_target_count` in addition to `special_section_toc_count`.
- `tests/test_front_matter_quality.py` and `tests/test_navigation_quality.py` now require zero front-matter TOC targets on the active release sample.
- Special-section routing is now validated together with nav-label quality and anchor correctness rather than only by heuristic scoring.

## Evidence

Release-gate checks on the tracked guard corpus:

- `strefa-pmi-52-2026`
  - `front_matter_target_count`: `0`
  - `special_section_toc_count`: `0`
  - `suspicious_nav_label_count`: `0`
  - `toc_entry_count`: `6`
  - `no_front_matter_toc_pollution`: `true`

- `newsweek-food-living-2026-01`
  - `front_matter_target_count`: `0`
  - `special_section_toc_count`: `0`
  - `suspicious_nav_label_count`: `0`
  - `toc_entry_count`: `15`
  - `no_front_matter_toc_pollution`: `true`

- `chess-5334-problems-combinations-and-games`
  - `front_matter_target_count`: `0`
  - `special_section_toc_count`: `0`
  - `suspicious_nav_label_count`: `0`
  - `toc_entry_count`: `4`
  - `no_front_matter_toc_pollution`: `true`

- `cover-letter-iwo-2026`
  - `front_matter_target_count`: `0`
  - `special_section_toc_count`: `0`
  - `suspicious_nav_label_count`: `0`
  - `toc_entry_count`: `1`
  - `no_front_matter_toc_pollution`: `true`

## GR4 Result

`PASS`

Special sections and front matter no longer degrade the tracked guard TOCs or article flow.

## Remaining Uncertainty

- The current proof is strong for the tracked guards, but broader mixed-layout and image-heavy publications still need more evidence before special-section routing can be considered globally complete.
