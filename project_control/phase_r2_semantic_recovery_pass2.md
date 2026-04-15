# Phase R2 Semantic Recovery Pass 2

Date: 2026-04-12
Sample: `samples/pdf/strefa-pmi-52-2026.pdf`
Active final EPUB: `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`

## Scope

This pass completed the current `R2-002` and `R2-003` recovery work:
- reconstruct cleaner article openings
- keep special sections out of article heading flow
- improve TOC quality without reintroducing navigation noise

## What Changed

1. Added a banner-aware article-opening override in `kindle_semantic_cleanup.py`.
   This allows a real title to become `h1` when a short section banner is followed by a strong title candidate and a byline or lead pattern.

2. Expanded special-section banner detection.
   Repeated banners and section labels such as `TEMAT NUMERU`, `KONTAKT`, `PROJEKT I SKLAD`, `STREFA PMI PC`, `STREFA NA LUZIE`, and membership-like labels are now much less likely to survive as article headings.

3. Re-ran the full active end-to-end sample:
   `PDF -> baseline EPUB -> final EPUB`

## Evidence Of Improvement

Compared with the previous R2 state:
- `page-0014.xhtml` now exposes a clean `h1`:
  `AI as a Mentor: Emerging Evidence on Human AI Collaboration in Project Management`
- content `h1_count` improved from `6` to `8`
- `toc_entry_count` improved from `5` to `8`
- TOC still has:
  - `0` page-label entries
  - `0` duplicate low-value entries
  - `0` author-only noise entries
  - `0` special-section TOC pollution entries
- special-section headings matched by the release-smoke special-section rule dropped from `7` to `0`
- technical validation still passes:
  - no dead nav targets
  - no dead NCX targets
  - no missing stylesheet references
  - title page still present and non-empty

## User-Visible Improvement

Material improvements now visible in the active final EPUB:
- more true article starts are exposed as `h1`
- article openings are calmer and less polluted by section banners
- TOC includes more real article entries without reintroducing `Page N` or author-only noise

## Remaining Gaps

The pass did not solve everything:
- `ISSUE-023` remains active because page-split XHTML still creates ambiguous continuation pages
- metadata is still release-blocking:
  - OPF title is still slug-like
  - creator is still `Unknown`
- text cleanup remains weaker than semantic recovery and still needs later recovery phases

## Gate Decision

`GR2`: PASS for the current `R2-002` and `R2-003` scope.

Reason:
- semantic structure materially improved
- special sections are more clearly segmented
- TOC quality improved without technical regression
- remaining ambiguity is explicit and tracked forward via `ISSUE-023` and `FX-012`
