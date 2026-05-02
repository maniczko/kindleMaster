# KindleMaster Conversion Profiles

This document is the operator-facing guide for choosing a conversion profile in the local UI. The UI shows the same short descriptions next to the profile selector so the user does not need to know the internal route names.

## Profiles

| UI profile | Use when | Primary goal |
| --- | --- | --- |
| `auto-premium` | The document type is unknown or mixed. | Let KindleMaster classify the source and apply release-grade quality gates. |
| `book` | The source is mostly text with chapters, headings, footnotes, references, or a conventional TOC. | Produce a reflowable EPUB with clean reading order, metadata, TOC, and Kindle-safe structure. |
| `magazine` | The source is editorial or layout-heavy, with articles, galleries, sidebars, ads, and recurring magazine sections. | Preserve article flow without promoting visual labels into fake chapters. |
| `technical-study` | The source is a business report, whitepaper, dense guide, or analytical document with tables, citations, references, and structured sections. | Preserve semantic tables, references, headings, and audit evidence. |
| `preserve-layout` | Reflow would likely damage the source, or the user needs a conservative fallback for visual layout. | Keep a safer layout-preserving result and report that it is not automatically a premium reflow EPUB. |

## Decision Rules

- Prefer `auto-premium` for normal use.
- Use `technical-study` for reports where tables, references, and metadata quality are more important than visual similarity to the PDF.
- Use `magazine` only for genuine magazine/editorial layouts, not simply because a report has images.
- Use `preserve-layout` as a review-oriented fallback, not as proof of premium Kindle reading quality.
- Use OCR only when the PDF has weak or missing text; OCR can improve scanned PDFs but may introduce review work.

## Quality Expectations

Each profile still feeds the same quality state and cockpit:

- download availability is separate from release readiness,
- `Publikuj` means release-grade evidence is clean enough,
- `Kontrola` means the EPUB can be read but needs review,
- `Nie publikuj` means blockers must be fixed before treating the EPUB as final.

