# OpenAI FEN Review Schema Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the OpenAI/AI chess FEN reviewer contract so AI remains review-only evidence with square-level uncertainty, never a source of verified labels, corpus promotion, EPUB mutation, or published FEN.

**Architecture:** Keep the existing OpenAI Responses-based provider and batch request exporters, but replace authority-like semantics with explicit review evidence. New schema fields capture square-level disagreements, side-to-move evidence, crop quality, and policy acknowledgement; import stores them only under `ai_*` fields.

**Tech Stack:** Python stdlib JSON/schema dictionaries, existing OpenAI Responses request construction, existing `chess_position_recognizer.validate_fen()`, existing JSONL review artifacts, `unittest`.

---

## 1. Current State Findings With File References

### `openai_chess_fen_reviewer.py`

- `OpenAIChessFenReviewer` is already review-only by design: returned payloads include `mode="review_only"`, `mutates_fen=False`, and `changed_output=False`.
- `propose_chess_fen_from_crop()` currently parses candidate output fields: `fen`, `side_to_move`, `confidence`, `uncertain_squares`, `reason`, and `needs_review`.
- `review_chess_fen()` currently parses review output fields: `approved`, `corrected_fen`, `requires_review`, `ambiguous_squares`, `issues`, `confidence`, and `notes`.
- `_candidate_payload()` and `_responses_payload()` both use Responses-style `input_image` payloads and strict JSON schema.
- `_candidate_schema()` lacks `square_diffs`, detailed side-to-move evidence, crop quality, and policy acknowledgement.
- `_review_schema()` still uses authority-like field names `approved` and `corrected_fen`; `_review_opinion()` softens this into `supports_candidate|flags_candidate|uncertain`, but the raw fields remain easy to misuse downstream.

### `scripts/export_chess_fen_review_queue.py`

- Review queue export is explicitly review-only and does not mutate labels or EPUB output.
- `_openai_label_assist_body()` builds batch Responses requests with crop image + deterministic evidence.
- `_openai_label_assist_schema()` uses the older schema: `approved`, `corrected_fen`, `requires_review`, `ambiguous_squares`, `issues`, `confidence`, `notes`.
- `_review_prompt()` correctly states that `approved=true` is only a review opinion, but still says "Accept a FEN" and "Promotion rule: approved/corrected items must be added..." which should be softened to "candidate can be copied into a manual draft only after visual verification."

### `scripts/build_chess_fen_label_aids.py`

- Uses `_openai_label_assist_body()` from the review queue exporter, so schema changes there automatically affect label-aid OpenAI requests.
- Generated manual templates are review-only and do not create corpus labels.

### `scripts/import_chess_fen_label_assist.py`

- Import is already safe in the most important way: it stores `ai_suggested_fen`, leaves `fen`, `verified_by`, and `verified_at` empty, sets `label_status="needs_manual_fen"`, and sets `accepted_for_corpus=False`.
- `_parse_response()` accepts direct rows containing `approved`, `corrected_fen`, and `requires_review`, plus nested Responses API bodies.
- Current gap: it does not preserve `square_diffs`, structured `side_to_move`, `cannot_verify_reason`, `evidence_level`, `crop_quality_notes`, or `policy_acknowledgement`.
- Current gap: invalid AI FEN is discarded from `ai_suggested_fen`, but related square/crop/side-to-move notes should remain available for manual review.

### `scripts/promote_chess_fen_label_draft.py`

- Promotion already requires `human_verified` and `square_diff_ack`.
- Promotion ignores AI-only suggestions when no manual FEN exists.
- Current schema hardening should not make promotion trust AI fields; it should only preserve AI evidence as metadata after human verification when useful.

### `test_chess_fen_recognition.py`

- Existing tests cover OpenAI review-only payloads, disabled-provider behavior, AI assist import, invalid AI FEN rejection, and review-only labels not being accepted for corpus.
- Current gap: tests do not require square-level AI diff preservation, structured side-to-move evidence, policy acknowledgement, or explicit rejection of authoritative fields such as `verified` or `accepted` in model output.

## 2. Target Review Schema

### Candidate Generation Schema

Use this for `propose_chess_fen_from_crop()` and AI FEN candidate passes:

```json
{
  "fen": "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1",
  "side_to_move": {
    "value": "b",
    "evidence": "caption",
    "confidence": 0.74
  },
  "confidence": 0.82,
  "uncertain_squares": ["g4"],
  "square_diffs": [],
  "cannot_verify_reason": "",
  "evidence_level": "ambiguous",
  "crop_quality_notes": ["piece edges blurred on kingside"],
  "reason": "Best-effort candidate; one occupied square is not fully clear.",
  "needs_review": true,
  "policy_acknowledgement": "review_only_no_corpus_promotion"
}
```

### Candidate Review Schema

Use this for `review_chess_fen()` and label assist review:

```json
{
  "review_opinion": "supports_candidate",
  "candidate_fen": "8/8/8/4p3/8/8/8/4K2k w - - 0 1",
  "suggested_fen": "8/8/8/4r3/8/8/8/4K2k w - - 0 1",
  "requires_review": true,
  "ambiguous_squares": ["e5"],
  "square_diffs": [
    {
      "square": "e5",
      "candidate_piece": "black pawn",
      "observed_piece": "black rook",
      "confidence": 0.91,
      "reason": "The piece silhouette has rook battlements, not a pawn head."
    }
  ],
  "side_to_move": {
    "value": "unknown",
    "evidence": "none",
    "confidence": 0.0
  },
  "issues": ["candidate_piece_mismatch"],
  "confidence": 0.68,
  "cannot_verify_reason": "",
  "evidence_level": "ambiguous",
  "crop_quality_notes": ["crop clear enough for piece identity but no side marker visible"],
  "notes": "Candidate has the wrong piece on e5.",
  "policy_acknowledgement": "review_only_no_corpus_promotion"
}
```

## 3. Field-By-Field Meaning

- `review_opinion`: Review evidence only. Allowed values: `supports_candidate`, `flags_candidate`, `uncertain`, `cannot_verify`.
- `candidate_fen`: The deterministic or upstream FEN being reviewed. It is not a label.
- `suggested_fen`: AI's suggested replacement. It is not a label and must map to `ai_suggested_fen` only during import.
- `approved`: Backward-compatible alias only. If retained, map to `review_opinion`; never use as authority.
- `corrected_fen`: Backward-compatible alias only. If retained, map to `suggested_fen` / `ai_suggested_fen`; never use as `fen`.
- `requires_review`: Must be `true` whenever any square, side-to-move, crop quality, or candidate-vs-crop disagreement is unresolved.
- `ambiguous_squares`: Square names where the model is not confident enough to support publication.
- `square_diffs`: Structured disagreements between candidate FEN and observed crop.
- `side_to_move.value`: `w`, `b`, or `unknown`.
- `side_to_move.evidence`: `marker`, `caption`, `inferred`, or `none`.
- `side_to_move.confidence`: Numeric confidence for side-to-move only, separate from piece recognition.
- `cannot_verify_reason`: Non-empty when the model cannot safely review the crop, for example `missing_crop`, `insufficient_crop`, `not_a_board`, or `image_too_blurry`.
- `evidence_level`: `clear`, `ambiguous`, `insufficient_crop`, or `missing_crop`.
- `crop_quality_notes`: Human-readable evidence; stored for review UI, not used for acceptance.
- `confidence`: Overall review confidence; never implies verification.
- `notes`: Freeform review explanation.
- `policy_acknowledgement`: Must equal `review_only_no_corpus_promotion`.

## 4. Prompt And Instruction Changes

- [ ] Replace "approved" wording in instructions with "review opinion".
- [ ] State that AI must prefer `requires_review=true` over guessing.
- [ ] State that AI must not support a candidate if any occupied square is ambiguous.
- [ ] State that AI must not infer side-to-move unless there is explicit marker or caption evidence; otherwise return `side_to_move.value="unknown"` and `side_to_move.evidence="none"`.
- [ ] Require `square_diffs` whenever AI disagrees with a candidate FEN.
- [ ] Require `evidence_level="insufficient_crop"` or `missing_crop` when crop evidence cannot support a visual judgement.
- [ ] Require `cannot_verify_reason` when `evidence_level` is not `clear` or `ambiguous`.
- [ ] Require `policy_acknowledgement="review_only_no_corpus_promotion"` in every model response.
- [ ] Explicitly forbid output fields named `verified`, `accepted`, `accepted_for_corpus`, `label_status`, `verified_by`, or `verified_at`.

## 5. Import Contract Changes

### `scripts/import_chess_fen_label_assist.py`

- [ ] Parse both legacy and v2 response shapes.
- [ ] Store all AI output under `ai_*` fields only:
  - `ai_suggested_fen`
  - `ai_review_opinion`
  - `ai_requires_review`
  - `ai_confidence`
  - `ai_ambiguous_squares`
  - `ai_square_diffs`
  - `ai_side_to_move`
  - `ai_cannot_verify_reason`
  - `ai_evidence_level`
  - `ai_crop_quality_notes`
  - `ai_policy_acknowledgement`
  - `ai_issues`
  - `ai_notes`
- [ ] Keep `fen=""`.
- [ ] Keep `verified_by=""`.
- [ ] Keep `verified_at=""`.
- [ ] Keep `human_verified=false` when present.
- [ ] Keep `label_status="needs_manual_fen"`.
- [ ] Keep `accepted_for_corpus=false`.
- [ ] Discard invalid AI FEN from `ai_suggested_fen`, but preserve `ai_square_diffs`, `ai_notes`, `ai_issues`, `ai_cannot_verify_reason`, and `ai_crop_quality_notes`.
- [ ] If `policy_acknowledgement` is missing or wrong, add `ai_policy_acknowledgement_missing` to `ai_issues`.
- [ ] If model output includes forbidden authoritative fields, ignore them and add `ai_authoritative_field_ignored` to `ai_issues`.

## 6. Export And Provider Changes

### `openai_chess_fen_reviewer.py`

- [ ] Add v2 candidate schema with `side_to_move` as an object, `square_diffs`, `cannot_verify_reason`, `evidence_level`, `crop_quality_notes`, and `policy_acknowledgement`.
- [ ] Add v2 review schema with `review_opinion`, `candidate_fen`, `suggested_fen`, `square_diffs`, structured `side_to_move`, `cannot_verify_reason`, `evidence_level`, `crop_quality_notes`, and `policy_acknowledgement`.
- [ ] Keep legacy `approved` and `corrected_fen` parsing for backward compatibility, but immediately normalize them to `review_opinion` and `suggested_label`.
- [ ] Drop unknown authoritative fields from parsed output.
- [ ] Return `mode="review_only"`, `mutates_fen=False`, and `changed_output=False` in every success and disabled/error path.

### `scripts/export_chess_fen_review_queue.py`

- [ ] Update `_openai_label_assist_schema()` to v2.
- [ ] Update `_openai_label_assist_body()` instructions to require square-level disagreement details.
- [ ] Update `_review_prompt()` examples to show `square_diffs`, structured `side_to_move`, and policy acknowledgement.
- [ ] Remove wording that sounds like AI "accepts" a FEN; use "supports candidate for human review".

### `scripts/build_chess_fen_label_aids.py`

- [ ] No separate schema should be duplicated; continue using the exporter helper after its schema is upgraded.
- [ ] Update README text in generated aids to explain AI response fields are prefill/review evidence only.

## 7. Tests To Add Or Update

### Provider Tests In `test_chess_fen_recognition.py`

- [ ] AI candidate response with `square_diffs` is parsed and returned as review-only metadata.
- [ ] AI review response with `square_diffs` maps to `review_opinion="flags_candidate"` when candidate and observed pieces differ.
- [ ] AI response with `policy_acknowledgement` missing is retained as review-only but reported with an issue.
- [ ] AI response containing forbidden `verified`, `accepted`, `label_status`, `verified_by`, or `verified_at` fields cannot affect returned provider status or labels.
- [ ] Disabled provider still returns review-only shape with new fields defaulted safely.

### Import Tests In `test_chess_fen_recognition.py`

- [ ] AI returns `square_diffs`; import preserves them as `ai_square_diffs`.
- [ ] AI returns `approved=true` and valid `corrected_fen`; import still creates manual draft only with `fen=""`, `verified_by=""`, `verified_at=""`, `label_status="needs_manual_fen"`, and `accepted_for_corpus=false`.
- [ ] AI cannot set `label_status="verified"` even if the response includes that field.
- [ ] AI invalid `corrected_fen` is rejected from `ai_suggested_fen`, while notes and square diffs remain preserved.
- [ ] Non-empty `ambiguous_squares` forces `ai_requires_review=true` or adds an issue if the model incorrectly returned `requires_review=false`.
- [ ] `side_to_move.evidence="inferred"` keeps the draft in manual review and never becomes a verified side-to-move source.

### Export Tests In `test_chess_fen_recognition.py`

- [ ] OpenAI label assist request schema requires `square_diffs`, structured `side_to_move`, `evidence_level`, `crop_quality_notes`, and `policy_acknowledgement`.
- [ ] OpenAI prompt text includes "review-only" and forbids corpus promotion.
- [ ] Prompt example no longer says AI approval is enough for promotion.

## 8. Acceptance Criteria

- AI reviewer output has enough evidence to express known visual failures such as: `e5 candidate black pawn, observed black rook`.
- `approved=true`, `corrected_fen`, high confidence, or `review_opinion="supports_candidate"` never creates a verified label.
- Import stores AI data only under `ai_*`.
- Import keeps `fen`, `verified_by`, and `verified_at` empty.
- Import keeps `accepted_for_corpus=false`.
- Invalid AI FEN is discarded from `ai_suggested_fen`, while review notes and square diffs are preserved.
- Ambiguous occupied squares force manual review.
- Side-to-move is `unknown` unless explicit evidence exists.
- Every AI payload acknowledges `review_only_no_corpus_promotion`.
- Existing runtime EPUB/HTML/PGN publication remains unchanged except safer AI-review metadata.

## 9. Rollout Plan

### Phase 1: Compatibility Parser

- [ ] Add v2 parser support while keeping legacy `approved/corrected_fen` input readable.
- [ ] Add tests proving legacy output remains review-only.
- [ ] Do not change generated request schema yet.

### Phase 2: Schema And Prompt Upgrade

- [ ] Update provider schemas and export schemas to v2.
- [ ] Update prompt wording and generated review prompt examples.
- [ ] Add tests for strict schema fields.

### Phase 3: Import And Dashboard Metadata

- [ ] Store v2 evidence in `ai_*` fields.
- [ ] Surface counts for `ai_square_diff_count`, `ai_cannot_verify_count`, and `ai_policy_acknowledgement_missing_count` in import summaries.
- [ ] Keep promotion unchanged except that it ignores all new AI evidence fields.

### Phase 4: Hardening Gate Integration

- [ ] Feed `ai_square_diffs` and `ai_ambiguous_squares` into validator/promotion hardening as unresolved review evidence.
- [ ] Ensure no profile/corpus gate treats AI evidence as verified.

## 10. Risks And Mitigations

- Risk: schema drift from older saved OpenAI responses.  
  Mitigation: keep legacy parser support and normalize old fields into v2 review-only fields.

- Risk: model returns more `requires_review=true`, reducing apparent automation.  
  Mitigation: this is correct behavior; the goal is fewer false positives, not optimistic acceptance.

- Risk: strict schema becomes too verbose for batch review.  
  Mitigation: keep bounded fields and short strings; preserve only review evidence needed for human correction and audit.

- Risk: downstream code misunderstands `suggested_fen`.  
  Mitigation: import writes it only as `ai_suggested_fen`, validator/promotion must require manual `fen`.

- Risk: existing UI/reports show "approved" from legacy responses.  
  Mitigation: display `review_opinion` and mark `approved` as deprecated alias in docs.

## 11. Files Likely To Modify During Implementation

- `openai_chess_fen_reviewer.py`
- `scripts/export_chess_fen_review_queue.py`
- `scripts/build_chess_fen_label_aids.py`
- `scripts/import_chess_fen_label_assist.py`
- `scripts/promote_chess_fen_label_draft.py`
- `test_chess_fen_recognition.py`
- `docs/chess-study-data-contracts.md`

## 12. Validation Commands

Run targeted tests after implementation:

```powershell
python -m unittest test_chess_fen_recognition.py
```

Run broader quick verification if provider/import/promotion contracts change together:

```powershell
python kindlemaster.py test --suite quick
```

Run static compile checks for touched Python modules:

```powershell
python -m py_compile openai_chess_fen_reviewer.py scripts/export_chess_fen_review_queue.py scripts/build_chess_fen_label_aids.py scripts/import_chess_fen_label_assist.py scripts/promote_chess_fen_label_draft.py
```

## 13. Self-Review Checklist

- The plan preserves AI as review-only evidence.
- The plan addresses square-level disagreements, including pawn-vs-rook on `e5`.
- The plan keeps backward compatibility for legacy `approved/corrected_fen` responses.
- The plan ensures import cannot set `fen`, `verified_by`, `verified_at`, `label_status=verified`, or `accepted_for_corpus=true`.
- The plan includes tests for schema, import safety, ambiguity handling, and invalid AI FEN rejection.

