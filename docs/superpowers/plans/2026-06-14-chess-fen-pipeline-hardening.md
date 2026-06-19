# Chess FEN Pipeline Hardening Plan

## Summary
Harden the chess FEN/PGN pipeline so AI remains review-only evidence and cannot create verified labels or published FEN. The current repo already has review queues, an OpenAI review-only provider, template/eval scripts, exact-crop verified label publishing, PGN replay gates, and dashboard artifacts. The main gap is provenance and visual-proof hardening: a valid/high-confidence/arbiter-approved FEN can still be visually wrong, so promotion must require human verification plus square-level diff evidence.

## Current Architecture Summary
- `publication_pipeline.py` routes chess PDFs into `pymupdf_chess_extractor.py` and carries chess FEN/PGN metadata into reports.
- `pymupdf_chess_extractor.py` publishes FEN only from deterministic recognition or verified exact-crop labels; `_scan_chess_apply_verified_crop_label()` is the runtime publication choke point.
- `chess_position_recognizer.py` owns `validate_fen()`, image/template recognition, confidence, warnings, and review-only AI review attachment.
- `openai_chess_fen_reviewer.py` uses vision payloads and explicitly returns `mode=review_only`, `mutates_fen=False`.
- Review/corpus scripts already exist for queue export, label aids, AI assist import, draft promotion, label validation, template build, recognizer eval, corpus eval, and profile readiness.
- PGN export in `chess_pgn_extractor.py` already blocks strict export on replay errors, move-number issues, and `unmapped_chess_glyphs`.

## What Already Works Well
- AI assist import stores `ai_suggested_fen` separately and leaves `fen`, `verified_by`, and `verified_at` empty.
- Template evaluation reports exact FEN accuracy, false positives, square accuracy, per-piece metrics, and confusion.
- Corpus/profile gates require minimum seed labels, label validation, exact accuracy, and zero false positives.
- Runtime verified-label publication is crop-hash scoped, which is the right publication safety model.
- Existing tests cover OpenAI review-only behavior and PGN strict export blockers.

## Critical Weaknesses
- `--accept-ai-suggestions` in draft promotion was too easy to misuse; it must never convert AI output into verified labels.
- Label validation checked FEN syntax and verifier fields, but not human provenance, crop hash, square-level diff acknowledgement, or AI-only contamination.
- OpenAI schema used risky terms such as `approved` and `corrected_fen`; these are review signals, not authority.
- High-confidence or arbiter-approved candidates can be visually wrong, as shown by `p010_d002`: candidate pawn on e5 versus crop rook on e5.
- Square-level diff existed as eval scoring, but not as a mandatory promotion/corpus gate signal.

## Target Architecture
- AI output is `review_signal_only`: candidate FEN, confidence, uncertain squares, square notes, and reviewer opinion only.
- Verified labels require explicit human provenance: `verification_source=human_visual`, `human_verified=true`, `verified_by`, `verified_at`, crop hash, and square-diff acknowledgement.
- Square-level FEN diff compares candidate/manual/template/AI placements as 64 cells and reports square names plus expected/actual pieces.
- Promotion flow is: AI/template candidate -> manual visual check -> square diff -> `validate_fen` -> verified label -> template/eval -> corpus/profile gate -> exact-crop runtime use.
- Profile readiness fails when labels are AI-only, missing provenance, missing square-diff evidence, or match a known-bad false-positive pattern.

## Ordered Task Breakdown
- [x] Add `scripts/audit_chess_fen_false_positives.py` to scan AI candidates, verified labels, positions, profile evals, and dashboard outputs for unsafe high-confidence or AI-approved candidates.
- [x] Add square-level FEN diff helper for 64-cell placement comparison.
- [x] Add `p010_d002` known-bad regression gate: candidate with pawn on e5 is blocked because expected/manual e5 is a black rook.
- [x] Harden `validate_chess_fen_labels.py` against AI-only provenance, missing crop hash, missing human verification, review-only statuses, missing square-diff acknowledgement, and known-bad mismatches.
- [x] Deprecate `--accept-ai-suggestions`; retained as a no-op compatibility flag that never copies AI FEN into verified labels.
- [x] Wrap OpenAI `approved` semantics with `review_opinion=supports_candidate|flags_candidate|uncertain`; old fields remain backward-compatible but non-authoritative.
- [x] Keep corpus/profile gates backed by the hardened label validator.
- [ ] Add CI upload for square-diff and false-positive audit reports when the workflow starts producing them regularly.
- [ ] Add richer review UI for 64-square diffs beyond the first audit/report pass.

## Files Modified
- `chess_fen_hardening.py`
- `scripts/audit_chess_fen_false_positives.py`
- `scripts/promote_chess_fen_label_draft.py`
- `scripts/validate_chess_fen_labels.py`
- `scripts/import_chess_fen_label_assist.py`
- `scripts/export_chess_fen_review_queue.py`
- `openai_chess_fen_reviewer.py`
- `docs/chess-study-data-contracts.md`
- `test_chess_fen_pipeline_hardening.py`

## Tests And Scenarios
- AI `approved=true`, high confidence, valid FEN, but no human verification cannot promote.
- `p010_d002` pawn-on-e5 candidate fails with square diff showing e5 mismatch.
- Verified label validation rejects missing `verification_source`, crop hash, square diff, or AI-only source for non-legacy rows.
- Profile readiness inherits hardened label validation.
- OpenAI reviewer payload/schema cannot emit authoritative `verified` or `accepted` fields.
- Existing legacy manual labels remain readable as `legacy_human_visual` until migrated.

## Acceptance Criteria
- No path can convert `ai_approved`, `arbiter_approved`, or high confidence into `label_status=verified`.
- `p010_d002` known-bad candidate is permanently blocked and appears in false-positive audit output.
- New verified labels have human provenance, crop hash, valid six-field FEN, valid kings, and square-diff evidence.
- Corpus/profile gates fail if any non-legacy label is AI-only, missing provenance, missing square diff, or has known-bad false-positive behavior.
- Runtime EPUB/HTML/PGN publication remains unchanged except that unsafe FEN stays review-only.

## Rollback Strategy
- Gates are additive and generated artifacts remain readable.
- If runtime publication regresses, revert only the validator/gate integration while preserving audit scripts and reports.
- Do not relax runtime exact-crop verified-label publication; prefer review-only output over false accepted FEN.

## Risks And Mitigations
- Overblocking valid labels: report exact missing gate and allow legacy manual rows until migration.
- Existing local labels lack new fields: treat manual-grid-review rows as `legacy_human_visual`, then migrate gradually.
- AI schema drift: strict parser drops unknown authoritative fields and records schema violations.
- Dashboard may show lower quality after hardening: treat this as honest quality, not regression.
- Manual review burden increases: use AI/template only to prefill and prioritize, never to verify.
