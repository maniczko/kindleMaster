# Kindle Previewer Manual QA Checklist

Use this as evidence for premium claims. Automated gates can mark an EPUB as ready to verify, but not final 10/10 without manual Kindle evidence.

## Samples

- PL magazine or layout-heavy issue.
- Dense handbook.
- Diagram/chess or training book.
- OCR scan.

## Checks Per Sample

- Import opens without blocking warnings.
- Cover, title, creator, publisher, and language look correct.
- TOC has useful navigation and no fake procedural entries.
- First article/chapter, a middle section, and a final appendix/back matter section read in the expected order.
- Five images/figures are legible on phone/tablet/e-ink previews.
- Five tables or transformed mappings are readable without destructive horizontal layout.
- Downloadable draft status is not confused with publish approval when the strict gate reports review or blockers.

## Evidence

Store local evidence under `reports/kindle_delivery/<date>/`:

- `previewer.md` with sample names, app version, result, and unresolved risks.
- Optional screenshots when available.
- `manifest.json` listing EPUB paths, checksums, and verdicts.
