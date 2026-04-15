# Phase R0-003: Overwrite and Regeneration Audit

Date: 2026-04-12
Owner: Lead / Orchestrator
Gate: `GR0`

## Question

Do later stages overwrite earlier improvements?

## Answer

There is no separate post-final stage that overwrites the final EPUB after `finalize_epub_for_kindle()` completes.

However, there is destructive regeneration inside the finalizer itself.

## Evidence

### 1. Navigation is regenerated destructively

`_rewrite_navigation()` always rewrites:

- `EPUB/nav.xhtml`
- `EPUB/toc.ncx`

The regenerated TOC is built from `toc_entries` collected from promoted headings, not from validated final semantic structure. It also uses `chapter_path.name`, which strips the `xhtml/` directory prefix and creates broken relative paths in the final package.

### 2. Generic chapter rewriting is destructive

`_process_chapter()` runs on all spine XHTML files except `cover.xhtml`.

That includes:

- normal page chapters
- front matter
- `title.xhtml`

This means generic heuristics rewrite title/front matter as if they were normal article chapters.

### 3. Final CSS intent is not preserved in the package

`_build_xhtml_document()` rewrites XHTML documents to reference:

- `style/default.css`

But `_write_default_css()` only writes to:

- `EPUB/style/default.css`

and only if that path already exists.

In the current package it does not exist, so the final EPUB keeps only:

- `EPUB/styles/baseline.css`

The final XHTML/nav/title files therefore reference a stylesheet that is not packaged.

## Conclusion

The pipeline is not failing because a later outer stage overwrites the final EPUB. It is failing because the finalizer itself combines cleanup, heading promotion, TOC regeneration, and document rebuilding in one destructive stage, and some of its rewritten outputs are technically invalid or semantically worse than baseline.
