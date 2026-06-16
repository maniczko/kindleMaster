# Chess HTML PGN Reading Order Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan.

**Goal:** Add an auditable hardening layer for chess book HTML generation so reading order, diagram placement, FEN linkage, PGN extraction, and Kindle-ready output quality can be inspected and regression-tested before a chess EPUB/HTML is treated as semantically reliable.

**Architecture:** Introduce a report-first `ChessReadingOrderAudit` contract fed by existing extraction metadata from `pymupdf_chess_extractor.py`, PGN records from `chess_pgn_extractor.py`, and publication metadata from `publication_pipeline.py`. The first implementation should produce JSON/HTML diagnostics without changing strict PGN/FEN export rules.

**Tech Stack:** Python stdlib dataclasses/JSON, existing PyMuPDF extraction metadata, BeautifulSoup only where already used by tests, existing `python-chess` PGN replay gates, existing HTML artifact plumbing through `extra_artifacts`.

---

## 1. Current State Findings

### `publication_pipeline.py`

- Chess-specific routes are already selected before generic publication assembly:
  - `extract_chess_notation_pdf_reflow(...)` for `chess-notation-collection`.
  - `extract_pdf_with_chess_support(...)` for `diagram_book_reflow`.
  - `extract_scanned_chess_pdf_with_support(...)` for scanned chess books.
- The pipeline already preserves `extra_artifacts`, emits `pdf_layout_preview`, and carries `chess_fen`, `chess_pgn`, and `reading_flow` metadata into the publication quality report.
- Current gap: the quality report exposes aggregate metadata, but not a page-by-page proof that the semantic sequence is still `heading -> explanatory text -> diagram -> candidate line/solution -> commentary`.

### `pymupdf_chess_extractor.py`

- The extractor already builds `book_layout_pages` and `ChessBookLayoutElement`-shaped dictionaries from text line items, diagrams, FEN, and PGN records.
- It attaches PGN records to layout pages with high reading-order values and creates diagram elements with `reading_order_start=10_000`, while text starts near zero.
- It has multiple chess routes that collect diagrams and line items differently, including scanned/OCR paths.
- Current gap: ordering is used for rendering, but there is no standalone report that explains why an element is attached to a page, whether a diagram is detached from nearby text, or whether PGN ended up far from its source paragraph.

### `chess_pgn_extractor.py`

- Strong PGN safety already exists:
  - `ChessPgnRecord` stores raw text, movetext, PGN, annotated PGN, source pages, warnings, final FEN, and glyph diagnostics.
  - Strict export is blocked by replay errors, parser failures, move-number jumps/regressions, side-to-move issues, and `unmapped_chess_glyphs`.
  - `build_combined_pgn(...)` exports only records that pass deterministic gates.
- Layout-aware HTML support already exists:
  - `ChessDiagramRecord`
  - `ChessBookLayoutElement`
  - `ChessBookLayoutPage`
  - `build_pgn_download_html(..., diagrams=..., book_layout_pages=...)`
  - `_match_diagrams_to_records(...)`
- Current gap: layout matching is rendered, but not independently audited for commentary preservation, page continuation, diagram-to-PGN proximity, caption quality, and suspicious order changes.

### `converter.py`

- Chess diagram HTML attrs, image alt text, and Kindle CSS exist.
- Chess technical blocks are treated specially so notation is not destroyed by generic cleanup.
- Current gap: final EPUB quality checks do not yet prove that chess images have stable dimensions, alt text, relative paths, copyable accepted FEN/PGN, and hidden review metadata without leaking local paths.

### `epub_text_artifacts.py`

- Chess notation classes are excluded from generic text artifact scoring.
- Current gap: this protects notation from cleanup false positives, but does not verify that cleaned output remains semantically ordered or that OCR artifact removal preserved legal moves.

### Existing tests

- `test_chess_pgn_extraction.py` covers many strict PGN blockers, annotated PGN comments/variations, OCR-spaced prose, castling, promotion, ChessBase markers, and layout-aware diagram HTML.
- `test_chess_notation_regression.py` covers figurine mapping and unmapped glyph warnings.
- `test_chess_notation_reflow.py` verifies extra artifacts and layout diagram presence in HTML.
- `test_smoke_chess_quality.py` measures chess diagram tags and figurines at a smoke level.
- Current gap: tests prove individual extraction/rendering behavior, but not an end-to-end reading-order audit with text + diagram + FEN + PGN + solution/commentary links on the same page or across page boundaries.

## 2. Target Audit Contract

Create a lightweight canonical report model. It may start as internal dataclasses converted to dictionaries, not a runtime dependency-heavy schema.

### `ChessReadingOrderReport`

Fields:

- `source_path`
- `source_title`
- `generated_at`
- `profile`
- `page_count`
- `summary`
- `pages`
- `links`
- `warnings`
- `strict_export_summary`

### `ChessReadingOrderPage`

Fields:

- `page_index`
- `page_number`
- `page_size`
- `elements`
- `diagram_ids`
- `pgn_record_ids`
- `fen_candidate_ids`
- `warnings`

### `ChessReadingOrderElement`

Fields:

- `id`
- `type`: `heading | text | caption | diagram | fen | pgn | solution | commentary | review_warning | page_image`
- `source_kind`: `rawdict | pymupdf_dict | ocr | diagram_detector | pgn_extractor | fen_recognizer | html_artifact`
- `page_number`
- `bbox`
- `reading_order`
- `visual_order`
- `text`
- `normalized_text`
- `record_id`
- `diagram_id`
- `crop_path`
- `image_dimensions`
- `fen_candidate`
- `pgn_status`
- `warnings`
- `confidence`

### `ChessDiagramPgnLink`

Fields:

- `diagram_id`
- `page_number`
- `crop_path`
- `caption`
- `candidate_fen`
- `fen_status`
- `nearby_text`
- `nearby_pgn_record_id`
- `nearby_movetext`
- `solution_block_id`
- `commentary_block_ids`
- `match_strategy`: `caption_exact | exercise_label | same_page_nearest | reading_order_proximity | carried_from_previous_page | unmatched`
- `match_confidence`
- `warnings`

## 3. Required Artifacts

### `html_reading_order_report.json`

Machine-readable report with:

- One `ChessReadingOrderPage` per PDF/source page.
- Text blocks in detected order.
- Diagram ids, crop paths, image dimensions, captions, and FEN candidates.
- PGN blocks with raw text, normalized movetext, annotated PGN status, source pages, and blocking warnings.
- Diagram/FEN/PGN/commentary links.
- Suspicious order warnings and detached diagram warnings.
- Strict export status summary.

### `html_reading_order_report.html`

Human review report with:

- Summary scorebar: pages, diagrams, linked diagrams, orphan diagrams, PGN records, accepted PGN, review PGN, FEN accepted/review.
- Per-page timeline in reading order.
- Diagram thumbnails with crop path and FEN status.
- PGN cards with accepted/review status and blocking warnings.
- Link evidence: why a diagram is matched to a PGN/solution block.
- Warning badges for detached diagrams, order inversions, unmatched captions, and page-continuation risk.
- Optional link to `pdf_layout_preview` when available, but no dependency on localhost.

## 4. Reading-Order Audit Rules

### Page ordering

- Elements are sorted by `page_number`, then explicit `reading_order`, then `bbox.y0`, then `bbox.x0`.
- If explicit reading order conflicts heavily with bbox order, emit `suspicious_order_change`.
- If a page has no extracted semantic elements but exists in PDF, emit `empty_semantic_page_represented`.

### Diagram proximity

Emit warnings:

- `diagram_without_caption`: no nearby caption/exercise label before or after the diagram.
- `diagram_detached_from_commentary`: nearest text block is beyond a configured vertical/reading-order distance.
- `diagram_without_pgn_or_fen`: no linked FEN candidate and no nearby PGN/review block.
- `diagram_caption_order_inversion`: caption appears after unrelated PGN/commentary when bbox indicates it should be before.

### PGN proximity

Emit warnings:

- `pgn_without_nearby_context`: PGN block has no preceding heading/caption/prose within page or previous page window.
- `pgn_source_page_mismatch`: PGN `source_pages` do not overlap linked diagram/commentary pages.
- `pgn_continuation_unlinked`: PGN appears to continue from previous page but no continuation link was recorded.
- `review_pgn_visible_without_reason`: review-only PGN appears in HTML without clear warning/reason.

### Text continuity

Emit warnings:

- `heading_text_gap`: heading followed by diagram/PGN without explanatory text when source page has intervening text blocks.
- `paragraph_split_across_diagram`: paragraph fragments surround a diagram but are not linked as context.
- `ocr_noise_promoted_to_visible_reader`: raw OCR/glyph junk appears in the user-facing reader instead of review metadata.

## 5. PGN Extraction Audit Plan

Extend PGN diagnostics in report mode without loosening strict export.

### Comments and variations

- Confirm `annotated_pgn` preserves prose comments inside `{ ... }`.
- Confirm RAVs are represented with `( ... )`, not square brackets.
- Report counts:
  - `comments_preserved`
  - `variations_preserved`
  - `square_bracket_variations_repaired`
  - `comments_dropped_or_reviewed`

### Move numbers and prose separation

- Detect move-number tokens merged with prose.
- Report when decimal engine values or comments are avoided as fake move numbers.
- Keep current blockers for `move_number_jump`, `move_number_regression`, `invalid_move_number_zero`, and wrong-side move numbers.

### Page continuation

- Add explicit report links for PGN records spanning multiple pages.
- Detect records whose raw text begins with continuation notation such as `...` or same move number without preceding context.
- Report `continuation_from_page` and `continuation_to_page` where inferred.

### SAN normalization

Audit these classes explicitly:

- Castling: `O-O`, `O-O-O`, and OCR variants with `0/o`.
- Promotion: compact promotion such as `b8Q`, `b8=Q`, check/mate suffixes.
- Check/mate: `+`, `#`, OCR substitutions, and ChessBase markers.
- Figurines: SPTimeFig, SPAriesFig, ChessBase private symbols, Woodpecker-style figurines.
- Captures and annotations: `x`, `!`, `?`, `!!`, `!?`, `+-`, `-+`, `=`.

### OCR artifact removal

- Every removal/normalization that affects movetext should record source token, normalized token, reason, and whether replay still passes.
- If replay fails after cleanup, keep record review-only and include cleanup trace in the report.

## 6. Diagram/FEN/PGN Linking Plan

Implement report-only linking first, then optionally reuse scores to improve HTML.

### Matching order

1. Exact caption/exercise match: `Diagram 1-2`, `Ex. 5-7`, `Exercise 3`.
2. Same page nearest caption and diagram bbox.
3. Reading-order proximity between diagram and PGN/solution/commentary.
4. Previous/next page continuation window for solutions split from diagrams.
5. Fallback to `unmatched`.

### Link scoring

Score components:

- `caption_match_score`
- `page_overlap_score`
- `bbox_distance_score`
- `reading_order_distance_score`
- `fen_status_score`
- `pgn_replay_score`
- `source_page_consistency_score`

Warnings:

- `low_confidence_link`
- `multiple_pgn_candidates`
- `multiple_diagram_candidates`
- `solution_far_from_diagram`
- `fen_pgn_side_to_move_conflict`

### Report usage

- `chess_pgn_html` can continue rendering current layout, but the report must expose when links are weak.
- Strict `chess_games.pgn` remains unchanged: linked PGN is exportable only if current deterministic PGN replay gates pass.

## 7. Kindle HTML Output Quality Checks

Add report checks for final generated HTML/EPUB fragments:

- Images have width/height or CSS-bounded dimensions.
- Chess diagram images have meaningful alt text.
- Diagram cards expose accepted FEN as copyable text only when validated.
- PGN cards expose copyable PGN only when parse/replay accepted.
- Review-only raw text is either hidden in metadata/details or report-only, never styled as accepted notation.
- No `localhost`, `127.0.0.1`, `file://`, or absolute local paths in final EPUB-facing HTML.
- Relative asset paths resolve in the EPUB manifest.
- No horizontal overflow risk in PGN/FEN blocks on Kindle-sized widths.
- Hidden metadata uses safe `data-*` attributes or JSON script blocks without leaking full raw OCR into visible text.

## 8. Ordered Task Breakdown

### Critical

- [ ] Add a report model and serializer for `ChessReadingOrderReport`.
- [ ] Add page-level audit extraction from existing `book_layout_pages`, diagrams, FEN candidates, and `ChessPgnRecord` data.
- [ ] Emit `html_reading_order_report.json` and `html_reading_order_report.html` as optional chess extra artifacts/reports.
- [ ] Add reading-order warnings for detached diagrams, PGN source-page mismatch, continuation risk, and order inversion.
- [ ] Add PGN audit diagnostics for comments, variations, move-number/prose separation, SAN normalization, and OCR cleanup traces.
- [ ] Ensure final HTML/EPUB quality checks block or report local paths, broken image references, empty copy controls, and review-only text promoted as accepted.

### Important

- [ ] Add diagram/FEN/PGN link scoring and confidence reasons.
- [ ] Add per-page review HTML with timeline, thumbnails, and linked PGN/FEN status.
- [ ] Extend publication quality metadata with reading-order audit summary counts.
- [ ] Add dashboard fields for orphan diagrams, orphan PGN, weak links, and page-continuation warnings.
- [ ] Add a runbook section explaining how to review reading-order reports before trusting generated chess study HTML.

### Optional

- [ ] Add visual overlay markers in `pdf_layout_preview` links for page/element anchors.
- [ ] Add CSV export of weak diagram/PGN links for manual review.
- [ ] Add deterministic sampling for accepted-looking records to catch silent reading-order false positives.

## 9. Files Likely To Modify

- `pymupdf_chess_extractor.py`: collect and pass richer page element/link metadata into report generation.
- `chess_pgn_extractor.py`: expose PGN audit details, link diagnostics, and HTML report helpers.
- `publication_pipeline.py`: attach reading-order audit summary to metadata/reports.
- `converter.py`: final HTML/EPUB quality checks for chess images, alt text, local paths, and copy controls.
- `epub_text_artifacts.py`: add report hooks if chess OCR junk appears in visible reader output.
- New likely helper: `chess_reading_order_audit.py` or `scripts/audit_chess_reading_order.py`.
- Tests:
  - `test_chess_pgn_extraction.py`
  - `test_chess_notation_reflow.py`
  - `test_chess_notation_regression.py`
  - `test_publication_pipeline.py`
  - `test_smoke_chess_quality.py`

## 10. Tests To Add Or Update

### Synthetic page: text + diagram + solution

- Fixture: one page containing heading, explanatory paragraph, diagram, solution paragraph, and legal PGN.
- Expected:
  - report order is heading -> text -> diagram -> solution/PGN;
  - diagram is linked to PGN;
  - no detached warning.

### Diagram before/after paragraph

- Fixture: two pages, one with caption before diagram and one with caption after diagram.
- Expected:
  - caption association works in both directions;
  - unrelated captions do not attach across long gaps.

### PGN continuation across page boundary

- Fixture: PGN starts on page N and continues on page N+1.
- Expected:
  - `source_pages` contains both pages;
  - report includes continuation link;
  - strict export remains blocked or accepted solely by replay result.

### Figurine notation

- Fixture: SPTimeFig/SPAriesFig/ChessBase/private/woodpecker-style figurines in notation.
- Expected:
  - known mappings normalize to SAN;
  - unknown glyphs produce review warnings;
  - report lists glyph context and affected PGN record.

### OCR noise around move numbers

- Fixture: engine evals, prose, OCR-spaced comments, and move numbers mixed together.
- Expected:
  - comments stay in `{ ... }`;
  - legal moves are not deleted;
  - decimal evals do not become move numbers;
  - cleanup trace appears in audit.

### Kindle HTML quality

- Fixture: generated chess reader HTML with diagrams, FEN, PGN, and review blocks.
- Expected:
  - no local paths;
  - no empty copy buttons;
  - accepted FEN/PGN are normal text/code;
  - review-only content is marked and not copyable as strict PGN.

## 11. Acceptance Criteria

- `html_reading_order_report.json` lists every represented page with text blocks, diagram ids, crop paths, PGN blocks, FEN candidates, warnings, and source-order evidence.
- `html_reading_order_report.html` gives a usable per-page review surface for reading order, diagram placement, and PGN/FEN linkage.
- Diagram/FEN/PGN links include match strategy, confidence, and explicit warnings for weak or detached links.
- PGN extraction audit proves comments, variations, SAN normalization, figurine mapping, OCR cleanup, and page continuations are either preserved or review-blocked.
- Final EPUB-facing HTML checks detect local links, broken image paths, missing alt text, empty copy controls, and review text promoted as accepted.
- Existing strict PGN/FEN export safety is not weakened.
- Tests cover synthetic source-order pages, diagram-before/after paragraph layouts, page-boundary PGN continuation, figurine notation, and OCR noise near move numbers.

## 12. Rollback Strategy

- Keep the audit report additive at first; do not change runtime export behavior until report quality is trusted.
- If report generation regresses conversion, disable the new artifact behind a config flag while preserving strict PGN/FEN gates.
- If HTML report rendering fails, still emit JSON and record `html_report_failed` in summary.
- If link scoring creates false alarms, tune warning thresholds without changing accepted PGN/FEN export.

## 13. Risks And Mitigations

- Risk: report over-warns on multi-column or exercise-solution layouts.
  - Mitigation: include match strategy and distances so warnings are actionable, not binary verdicts.
- Risk: scanned/OCR routes lack precise bbox for all text.
  - Mitigation: require explicit `source_kind=ocr` and lower confidence rather than inventing geometry.
- Risk: PGN continuation detection can be ambiguous.
  - Mitigation: keep continuation inference as audit evidence; deterministic replay remains the export authority.
- Risk: report artifacts become large for full books.
  - Mitigation: JSON stores compact metadata; HTML uses thumbnails and collapsible details.
- Risk: developers mistake audit-linked PGN as accepted PGN.
  - Mitigation: retain existing strict export rules and label every audit-only candidate as review evidence.
