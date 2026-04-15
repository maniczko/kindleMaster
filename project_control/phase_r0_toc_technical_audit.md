# Phase R0-004: TOC Technical Audit

Date: 2026-04-12
Owner: TOC & Navigation
Gate: `GR0`

## Files Audited

- baseline: `kindlemaster_runtime/output/baseline_epub/strefa-pmi-52-2026.epub`
- final: `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`

## Baseline Result

Baseline navigation is technically consistent:

- `nav.xhtml` entries point to `xhtml/page-XXXX.xhtml#page-XXXX`
- `toc.ncx` entries use the same valid relative structure
- no path failures were detected in baseline navigation

## Final Result

Final navigation is technically broken:

- `nav.xhtml` points to `page-0001.xhtml#...`
- `toc.ncx` points to `page-0001.xhtml#...`
- actual content files remain stored in `EPUB/xhtml/page-0001.xhtml`

This drops the required `xhtml/` directory prefix and breaks relative navigation.

## Measured Findings

- final `toc_entry_count`: `145`
- final `noisy_toc_entry_count`: `87`
- final `duplicate_toc_entry_count`: `5`
- dead-link candidates: widespread across the TOC

Examples of noisy or invalid final TOC entries:

- `Page 1`
- `Julia Janiszewska`
- `Bartosz Misiurek`
- `S. 8`
- `ZESPÓŁ ZARZĄDZAJĄCY`
- `Redaktor Naczelna`

## Root Technical Defect

The final TOC is built from:

- weak heading promotion
- unfiltered front-matter and staff-page content
- incorrect relative path generation

## Conclusion

The final EPUB navigation regressed both technically and semantically. The current TOC is not just noisy; it is also structurally invalid for package-relative paths.
