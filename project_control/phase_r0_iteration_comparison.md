# Phase R0-001: EPUB Iteration Comparison

Date: 2026-04-12
Owner: QA / Regression
Gate: `GR0`

Compared artifacts:

1. `samples/epub/strefa-pmi-52-2026.epub`
2. `kindlemaster_runtime/output/baseline_epub/strefa-pmi-52-2026.epub`
3. `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`

## Metric Comparison

| Metric | Existing EPUB | PDF Baseline EPUB | Final EPUB | Classification |
| --- | ---: | ---: | ---: | --- |
| `h1_count` | 0 | 1 | 0 | regression vs baseline; no material recovery |
| `h2_count` | 58 | 3 | 149 | regression; strong over-promotion |
| `h3_count` | 50 | 0 | 186 | regression; strong over-promotion |
| `paragraph_count` | 492 | 1839 | 782 | no reliable improvement; structure was rewritten, not cleaned |
| `toc_entry_count` | 26 | 84 | 145 | regression |
| `noisy_toc_entry_count` | 0 | 84 | 87 | regression |
| `duplicate_toc_entry_count` | 0 | 0 | 5 | regression |
| `page_label_count` | 0 | 122 | 84 | partial reduction, but still user-visible noise |
| `split_word_count` | 2 | 3 | 3 | no material change |
| `joined_word_count` | 14 | 18 | 18 | no material change |
| `suspicious_paragraph_join_count` | 440 | 933 | 651 | partial improvement only |
| `metadata_quality_check` | ok | weak title, weak creator | weak title, weak creator | no material change |

## Semantic Comparison

Baseline to final:

- `title.xhtml` regressed from a minimal title page to an empty section.
- page labels such as `Page 1` became headings in the final EPUB.
- author names and staff names were promoted to `h2`.
- front matter and organizational sections became TOC candidates.
- the final EPUB has no meaningful `h1` structure across the spine.

Existing EPUB reference versus final:

- the existing EPUB already has lower TOC noise and better navigation discipline.
- the generated final EPUB is more aggressive structurally, but not more readable.
- the final EPUB is materially worse for navigation than the existing EPUB fixture.

## Real Improvement

- suspicious paragraph-join count dropped from `933` to `651`
- page-label count dropped from `122` to `84`

## No Material Change

- split-word count remained `3`
- joined-word count remained `18`
- metadata stayed weak: title and creator remained generic

## Regression

- TOC count increased from `84` to `145`
- noisy TOC entries increased from `84` to `87`
- duplicate TOC entries increased from `0` to `5`
- heading structure degraded into `0 h1 / 149 h2 / 186 h3`
- navigation targets became technically broken in the final EPUB

## Conclusion

Recent iterations did apply changes to the final EPUB, but those changes did not produce a net Kindle reading improvement. The dominant effect was destructive semantic promotion and destructive TOC regeneration, not meaningful cleanup of real PDF artifacts.
