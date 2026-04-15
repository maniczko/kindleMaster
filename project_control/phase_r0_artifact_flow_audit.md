# Phase R0-002: Artifact Flow Audit

Date: 2026-04-12
Owner: Lead / Orchestrator
Gate: `GR0`

## Artifact Lifecycle Observed

1. Source PDF:
   `samples/pdf/strefa-pmi-52-2026.pdf`
2. Baseline EPUB:
   `kindlemaster_runtime/output/baseline_epub/strefa-pmi-52-2026.epub`
3. Final EPUB:
   `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`
4. End-to-end trace:
   `kindlemaster_runtime/output/reports/strefa-pmi-52-2026-end-to-end.json`

## Stage Ownership

- `kindlemaster_pdf_to_epub.py`
  creates the baseline EPUB from PDF.
- `kindlemaster_end_to_end.py`
  runs baseline conversion, then calls `finalize_epub_for_kindle()`.
- `kindle_semantic_cleanup.py`
  extracts the baseline EPUB into a temp directory, rewrites content, repacks the final EPUB.

## What Was Actually Modified

Baseline to final comparison shows changed members in:

- `EPUB/content.opf`
- `EPUB/nav.xhtml`
- `EPUB/toc.ncx`
- `EPUB/title.xhtml`
- all `EPUB/xhtml/page-0001.xhtml` through `page-0084.xhtml`

There were:

- no added package members
- no removed package members

## Did Final Output Include Intended Changes?

Yes, the final EPUB includes widespread rewritten content. The problem is not “changes stayed in intermediate artifacts only”.

What is true:

- final XHTML files are different from baseline XHTML files
- final navigation files are different from baseline navigation files
- final metadata file is different from baseline metadata

What is also true:

- some intended final-stage changes never actually shipped correctly, especially the Kindle CSS file path
- the most visible final-stage changes are destructive, not beneficial

## Conclusion

The final output does include remediation-stage changes. The failure is not a missing handoff from intermediate to final artifact. The failure is that the final-stage rewrite itself introduces broken navigation, broken CSS linkage, and poor semantic promotion.
