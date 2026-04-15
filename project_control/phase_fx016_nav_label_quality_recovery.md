# FX-016 - Nav Label Quality Recovery

## Goal

Recover representative Kindle bookmark labels so `nav.xhtml` and `toc.ncx` reflect real article or record titles instead of truncated fragments, dangling parenthetical records, or weak context-free labels.

## Root Cause

- final nav labels inherited weak `h1` candidates too literally
- merged `title + kicker` strings were not always split when the merged block was short
- dangling title fragments such as `Na casting do` were not completed from adjacent opening context
- book-like chess records could keep open parenthetical metadata because the closing year sat in a later paragraph
- release smoke and quality scoring previously did not count truncated bookmark labels as a navigation regression

## Implemented Fixes

- relaxed merged-opening recovery so compact `title + kicker` headings can split into a cleaner title and lead
- added context-aware nav label derivation for article openings
- completed short dangling labels from adjacent lead phrases when the completion was deterministic
- combined generic travel/section labels with short lead phrases when that produced a more representative bookmark
- scanned short following paragraph windows for closing year metadata to repair chess record labels like `(... - 1933)`
- added release-smoke counting for suspicious nav labels
- added scenario and navigation tests so truncated or dangling bookmark labels fail pytest

## Evidence

### Magazine guard improvements

- `Na casting do` -> `Na casting do MasterChefa`
- `W drodze ku smakom` -> `W drodze ku smakom - Śniadania jak w domu`
- `Gault & Millau` context is now preserved in the final bookmark label

### Book guard improvements

- `Helbig – Schroder (Place unknown` -> `Helbig – Schroder (Place unknown - 1933)`
- `Young – Barden (Correspondence` -> `Young – Barden (Correspondence - 1945)`
- `suspicious_nav_label_count` is now `0`

## Validation

- `python -m pytest -q` -> `33 passed`
- `python kindlemaster_quality_score.py --epub kindlemaster_runtime/output/final_epub/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.epub --publication-id newsweek-food-living-2026-01`
  - `weighted_score: 10.0`
  - `no_truncated_or_dangling_nav_labels: true`
- `python kindlemaster_quality_score.py --epub kindlemaster_runtime/output/final_epub/tactits.epub --publication-id chess-5334-problems-combinations-and-games`
  - `weighted_score: 8.65`
  - `suspicious_nav_label_count: 0`
  - `no_truncated_or_dangling_nav_labels: true`
- `python kindlemaster_release_gate_enforcer.py --publication-id strefa-pmi-52-2026`
  - smoke: `PASS`
  - pytest: `33 passed`
  - final verdict: still `FAIL` only because pre-existing high-severity release blockers remain open
