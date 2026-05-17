# KindleMaster Premium Warning Policy

This policy keeps `passed_with_warnings` explicit. A warning may be accepted as P2 only when it is bounded, visible in reports, and does not imply `premium_ready=true`.

## Accepted P2 Warnings

| Code | Acceptance condition |
| --- | --- |
| `pre_heading_epubcheck_recovered` | Final heading-repaired EPUBCheck status is `passed`. |
| `text_artifact_rate_review` | Low artifact count/rate and no hard visible OCR junk. |
| `heading_manual_review` | Final EPUBCheck passed, review count is small, and a known route owns the review. |
| `reference_empty_section_review` | No citations, no visible junk, and at most one empty section remains. |
| `reference_review_needed` | Magazine reference-like text has no citations, visible junk, or unresolved fragments. |
| `magazine_premium_quality_review` | Only bounded URL/masthead review or low-resolution image review is present. |

## Repair Warnings

These remain active review work and should keep the corpus yellow until fixed or manually accepted with evidence:

- `magazine_article_title_truncated`
- `magazine_article_segmentation_needs_review`
- `magazine_non_editorial_sections_present`
- `magazine_premium_score_below_9`
- visible OCR/glued/split text
- TOC/article coverage gaps

## Operational Rule

`download_available` means an EPUB draft can be inspected. It is not publication approval. `pass_with_review` and `passed_with_warnings` must never set `premium_ready=true`.
