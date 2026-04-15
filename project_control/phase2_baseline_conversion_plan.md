# Phase 2 Baseline Conversion Plan

Date: `2026-04-11`

## Validated Path

1. read repo-local PDF from `samples/pdf/`
2. extract page text with PyMuPDF
3. classify each page as text-first or image-fallback
4. build deterministic baseline EPUB package with:
   - `mimetype`
   - `META-INF/container.xml`
   - `EPUB/content.opf`
   - `EPUB/nav.xhtml`
   - `EPUB/toc.ncx`
   - XHTML pages
   - image fallback pages where text is too sparse
5. write baseline EPUB into `kindlemaster_runtime/output/baseline_epub/`
6. run Kindle remediation through `kindle_semantic_cleanup.py`
7. write final EPUB into `kindlemaster_runtime/output/final_epub/`

## Output Isolation

- no Vite output
- no frontend runtime
- no foreign frontend/runtime build path
- no shared output directory with the foreign application

## Current Validated Sample

- source PDF: `samples/pdf/strefa-pmi-52-2026.pdf`
- baseline EPUB: `kindlemaster_runtime/output/baseline_epub/strefa-pmi-52-2026.epub`
- final EPUB: `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`
