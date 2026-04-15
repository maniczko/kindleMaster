# Phase R2 Semantic Recovery Pass 1

Date: 2026-04-12
Owner: Structure & Semantics
Related tasks: `R2-001`, `R2-002`, `FX-010`

## Scope

This pass focused on safer title detection and TOC protection after the R1 integrity fixes.

Implemented changes:
- added chapter-level profile detection for `toc`, `front_matter`, `back_matter`, `promo`, and `article`
- stopped automatic promotion of special-section banners into navigable headings
- blocked TOC candidates that look like page labels, bibliography/reference markers, numbered subsection labels, and short banner-like residues
- kept person-name, role, and dash-led interview/byline lines as paragraph-level content
- added a narrow interview-opening rule so a real question-style article title can become `h1` while the following dash-led line stays a byline paragraph
- kept title selection conservative so weak or noisy candidates no longer dominate final navigation

## Evidence

Measured on:
- `samples/pdf/strefa-pmi-52-2026.pdf`
- baseline EPUB: `kindlemaster_runtime/output/baseline_epub/strefa-pmi-52-2026.epub`
- final EPUB: `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`

Before vs after:
- `toc_entry_count`: `84 -> 5`
- `noisy_toc_entry_count`: `84 -> 0`
- `duplicate_toc_entry_count`: `0 -> 0`
- `h1_count`: `1 -> 6`
- `h2_count`: `3 -> 10`
- `h3_count`: `0 -> 68`
- `paragraph_count`: `1839 -> 762`
- `split_word_count`: `2 -> 2`
- `joined_word_boundary_count`: `182 -> 173`

Validated technical integrity:
- `final_validation.pass = true`
- `nav_dead_targets = []`
- `missing_stylesheets = []`
- `title_page_empty = false`

## Real improvements

- TOC noise materially collapsed; section hubs, bibliography, sponsor blocks, and masthead-like entries are no longer flooding navigation.
- Mixed front matter is less destructive because section banners and person-role lines are no longer promoted into article-level TOC entries by default.
- Interview-style opening on `page-0050.xhtml` now has a clean `h1` title plus paragraph-level byline instead of a false byline heading.
- The false `h1` created from a mid-page continuation paragraph on `page-0007.xhtml` was removed.

## Remaining problems

- Cross-page article continuity is still weak. Some real article starts remain under-detected because the PDF->EPUB baseline currently splits magazine content page-by-page into separate XHTML files.
- This causes under-selection in TOC: several genuine article starts remain `h2`/`h3` or plain paragraphs instead of article-level `h1`.
- `page-0014.xhtml` still keeps the real article title as `h3` instead of `h1`.
- `page-0071.xhtml` still looks ambiguous: it may be an article start or a continued section, and the current heuristic stays conservative.

## Recovery verdict for this pass

`R2-001` is materially improved and no longer dominated by the earlier false-title patterns.

`R2-002` remains active because the pipeline still needs a safer way to distinguish:
- true article openings
- continued pages inside the same article
- special-section carry-over pages

## Next action

Proceed to `R2-002` with a dedicated recovery step for multi-page article openings and continuation detection.
