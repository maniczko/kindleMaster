# Phase 1 Input Requirements

`T1-001` cannot complete until at least one real EPUB input package is available in the repository or in an agreed local input path.

## Minimum Unblock Requirement

- 1 valid EPUB package for inventory and baseline analysis
- package contains `content.opf` or equivalent package document
- package contains navigational content such as `nav.xhtml` or equivalent
- package contains XHTML or HTML content documents
- package contains CSS and at least one referenced asset if images are part of the publication

## Recommended Generic Corpus

- 1 `book_like` sample
- 1 `report_like` sample
- 1 `magazine_like` sample
- 1 `mixed_layout` sample

## Placement Guidance

- preferred local directory: `samples/epub/`
- keep original input files read-only
- do not hardcode logic against one publication

## Current Input Status

Resolved on `2026-04-11`: Phase 1 is now using the preferred repo-local input path `samples/epub/`.

Verified repo-local inputs:

- `9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411 (3).epub`
- `strefa-pmi-52-2026.epub`
- `tactits (2).epub`

All three packages were verified as valid EPUB zip containers with `META-INF/container.xml`, `EPUB/content.opf`, `EPUB/nav.xhtml`, XHTML content, CSS, and assets.

## Escalation

- keep original inputs read-only
- continue with Phase 1 and later phases using `samples/epub/` as the preferred local corpus
