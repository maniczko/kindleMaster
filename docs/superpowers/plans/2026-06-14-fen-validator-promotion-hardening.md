# Chess FEN Validator And Promotion Hardening

## Summary

This plan hardens KindleMaster's chess FEN label validation and promotion flow so no AI or high-confidence candidate can become a verified/profile-ready label without explicit human visual verification against the crop.

The current implementation already has good safety foundations: AI label assist writes review-only fields, promotion requires `human_verified`, profile readiness calls label validation, and the FEN recognizer has a custom `validate_fen()` gate. The remaining risk is that validation still accepts some ambiguous or underspecified rows by omission: `label_status` is not required to be exactly `verified`, promotion does not fail early on all unresolved AI-review signals, and validation does not yet add a `python-chess` status layer.

## 1. Current State Findings With File References

### `scripts/validate_chess_fen_labels.py`

- `validate_chess_fen_labels()` is the central label gate used by corpus/profile checks.
- `_record_issues()` validates `fen`, calls `chess_position_recognizer.validate_fen()`, requires `verified_by`, `verified_at`, crop existence, human provenance, square diff acknowledgement, and crop hash for non-legacy rows.
- Current gap: `label_status` is only rejected when it is in `REVIEW_ONLY_STATUSES = {"needs_manual_fen", "placeholder", "draft", "review_required"}`. It does not require `label_status == "verified"`.
- Current gap: unresolved ambiguity is partially covered by AI-only provenance checks, but there is no explicit blocker list for fields such as `ai_requires_review`, `ai_ambiguous_squares`, unresolved `ai_issues`, or manual review flags.
- Current gap: validation uses project-local FEN checks, but not the installed `chess` dependency for an independent `python-chess` parse/status pass.

### `scripts/promote_chess_fen_label_draft.py`

- Promotion already requires `human_verified` and `square_diff_ack`.
- Promotion ignores missing manual FEN when only `ai_suggested_fen` or `deterministic_suggested_fen` exists.
- Promotion writes `label_status="verified"`, `label_source`, `ai_assisted`, `verification_source="human_visual"`, `human_verified=True`, `square_diff_ack=True`, and preserves `square_diff`.
- `--accept-ai-suggestions` exists but is documented as a deprecated no-op.
- Current gap: promotion should reject rows with unresolved AI-review flags such as `ai_requires_review=true`, not just avoid copying the AI FEN.
- Current gap: promotion computes `crop_sha256` if the crop exists, but should fail promotion if neither `crop_path` nor `source_crop_path` exists.

### `scripts/import_chess_fen_label_assist.py`

- AI assist import is correctly review-only: it stores `ai_suggested_fen`, `ai_approved`, `ai_requires_review`, `ai_confidence`, `ai_ambiguous_squares`, and `ai_issues`.
- It leaves `fen`, `verified_by`, and `verified_at` empty and sets `label_status="needs_manual_fen"`.
- This is sufficient as an ingest contract, but downstream validators must treat every `ai_*` field as evidence only.

### `scripts/check_chess_fen_profile_ready.py`

- Profile readiness calls `validate_chess_fen_labels()` before building/evaluating templates.
- It sets `accepted_for_corpus` only when validation and recognizer evaluation pass.
- This becomes sufficiently safe only if `validate_chess_fen_labels()` enforces the stricter verified-label contract.

### `scripts/evaluate_chess_fen_corpus.py`

- Corpus evaluation calls `validate_chess_fen_labels()` for seed label profiles.
- Font-board candidate profiles are explicitly kept `accepted_for_corpus=False`.
- This is the right integration point for stricter validation and python-chess status summaries.

### `chess_position_recognizer.py`

- `validate_fen()` checks six FEN fields, piece placement, king counts, side to move, castling syntax, en passant syntax, and move counters.
- It is useful and should remain the first project-local validation layer.
- Current gap: it should be complemented by `python-chess` parsing/status classification so corrupted but syntactically plausible FENs are reported more clearly.

### `requirements.txt`

- `chess>=1.11,<2` is already installed as a first-class dependency, so adding a `python-chess` validation layer does not require a new package.

### `test_chess_fen_recognition.py`

- Existing tests cover AI review-only import, AI suggestion not being promoted without manual FEN, deterministic suggestion not being promoted without manual FEN, review-only labels being rejected, and profile readiness/corpus gates.
- Current gap: tests should assert exact `label_status == "verified"`, explicit human verification, crop existence at promotion time, unresolved AI-review blockers, and `python-chess` blocker/warning classification.

## 2. Target Contract

A FEN label is eligible for template/profile/corpus use only when all of these are true:

- `label_status == "verified"`.
- `human_verified is true`.
- `verification_source == "human_visual"` or another explicitly allowed human source.
- `verified_by` is present and non-empty.
- `verified_at` is present and ISO/date-like.
- `fen` is present and is the manually verified FEN, not an AI-only candidate field.
- `crop_path` or `source_crop_path` exists.
- `crop_sha256` is present and matches for new non-legacy rows.
- `square_diff_ack` is present for non-legacy rows.
- Project `validate_fen()` passes.
- `python-chess` parse/status pass has no blocker status.
- No unresolved review-only/ambiguity fields remain.

The validator should treat `ai_approved`, `arbiter_approved`, high confidence, and `ai_requires_review=false` as non-authoritative evidence. They may help prioritize review; they never imply verification.

## 3. Planned Changes

### Critical: Strengthen `validate_chess_fen_labels.py`

- [ ] Require `label_status == "verified"` for every non-legacy corpus/profile label.
- [ ] Keep a temporary `legacy_manual` compatibility path only if unavoidable, but report it as `legacy_label_contract` and make it ineligible for new profile readiness unless explicitly allowed.
- [ ] Require `verified_by`.
- [ ] Require `verified_at`.
- [ ] Require `human_verified is true`.
- [ ] Require `verification_source` to resolve to a human source.
- [ ] Require existing `crop_path` or `source_crop_path`.
- [ ] Require `crop_sha256` for new rows and validate it when present.
- [ ] Require `square_diff_ack` for new rows and preserve existing square-level diff evidence.
- [ ] Reject review-only statuses and any non-`verified` status with a dedicated issue code such as `label_status_not_verified`.
- [ ] Reject unresolved ambiguity fields:
  - `ai_requires_review is true`.
  - `ai_ambiguous_squares` is non-empty.
  - `ambiguous_squares` is non-empty.
  - `unresolved_ambiguity` is truthy.
  - `human_rejected` is truthy.
  - `review_required`, `requires_review`, or `needs_review` is truthy.
  - `ai_issues` contains blocker-level issues.
- [ ] Keep placeholder/note checks, but make them a secondary safety net rather than the primary status gate.

### Critical: Add `python-chess` Validation Layer

- [ ] Add a helper such as `classify_python_chess_fen_status(fen) -> {passed, blockers, warnings, status_flags}`.
- [ ] Use `chess.Board(fen)` for parsing.
- [ ] Use `board.status()` or available `chess.STATUS_*` constants to classify structural issues.
- [ ] Treat these as blockers:
  - parse error;
  - missing white or black king;
  - too many kings;
  - pawns on back rank;
  - too many pieces or pawns;
  - empty board;
  - impossible opposite-check style status;
  - side-to-move impossible because the position is structurally invalid.
- [ ] Treat these as warnings unless the source explicitly requires them:
  - castling rights inconsistent with current piece placement;
  - en-passant square questionable for a diagram position;
  - halfmove/fullmove metadata normalized by the pipeline.
- [ ] Do not over-block study/exercise positions merely because they may not be reachable from a legal game history.
- [ ] Record clear issue codes such as `python_chess_parse_error`, `python_chess_blocker_status`, and `python_chess_warning_status`.

### Critical: Harden `promote_chess_fen_label_draft.py`

- [ ] Keep requiring `human_verified=true` per row.
- [ ] Fail promotion when `label_status` is already a review-only status unless the row also has a valid manual FEN and explicit human verification fields.
- [ ] Remove `--accept-ai-suggestions` in a breaking cleanup, or keep it temporarily as a no-op that emits a warning and never affects output.
- [ ] Reject rows where `ai_requires_review=true`.
- [ ] Reject rows with non-empty `ai_ambiguous_squares` or unresolved blocker-level `ai_issues`.
- [ ] Reject rows without an existing `crop_path` or `source_crop_path`.
- [ ] Require or compute `crop_sha256`; if the crop exists but hashing fails, skip the row.
- [ ] Store `label_source="manual_fen"` for promoted rows.
- [ ] Store `ai_assisted=true|false`, but never let this become authority.
- [ ] Preserve `square_diff`, `square_diff_ack`, and any visual proof metadata in the promoted output.
- [ ] Run both project `validate_fen()` and the new `python-chess` classification before writing a promoted row.

### Important: Harden Profile And Corpus Gates

- [ ] `check_chess_fen_profile_ready.py` should surface new validator issue categories in `next_required_actions`.
- [ ] `evaluate_chess_fen_corpus.py` should include label provenance and python-chess status summaries in each case.
- [ ] Profile readiness should fail if any seed label is:
  - not exactly `verified`;
  - missing human provenance;
  - AI-only or AI-ambiguous;
  - missing crop evidence;
  - missing square-diff acknowledgement;
  - failing python-chess blockers.

### Important: Documentation And Runbook

- [ ] Update `docs/chess-study-data-contracts.md` with the verified-label contract.
- [ ] Add a human labeling runbook section:
  - inspect crop visually;
  - correct FEN manually;
  - compare square diff;
  - acknowledge diff;
  - set `human_verified=true`;
  - fill `verified_by` and `verified_at`;
  - run label validation;
  - run profile readiness/eval;
  - never trust AI approval alone.

## 4. Proposed Validator Issue Codes

- `label_status_missing`
- `label_status_not_verified`
- `human_verified_missing`
- `verification_source_missing`
- `verification_source_not_human`
- `verified_by_missing`
- `verified_at_missing`
- `crop_path_missing`
- `crop_path_not_found`
- `crop_sha256_missing`
- `crop_sha256_mismatch`
- `square_diff_ack_missing`
- `ai_review_unresolved`
- `ai_ambiguous_squares_unresolved`
- `review_only_note_or_status`
- `fen_invalid_project_validator`
- `python_chess_parse_error`
- `python_chess_blocker_status`
- `python_chess_warning_status`

The validator should return both row-level issues and aggregate counts so profile/corpus reports can explain exactly why a profile is not ready.

## 5. Python-Chess Classification Policy

The project-local `validate_fen()` remains authoritative for KindleMaster-specific publication requirements. `python-chess` becomes an independent structural validator.

Recommended result shape:

```json
{
  "python_chess": {
    "passed": false,
    "blockers": ["missing_black_king"],
    "warnings": ["bad_castling_rights"],
    "status_flags": ["STATUS_NO_BLACK_KING", "STATUS_BAD_CASTLING_RIGHTS"]
  }
}
```

The key policy point is conservative but not overzealous: study positions can be valid diagram positions even when full game-history legality is unknown. The validator should block impossible board states, not reject every position with incomplete metadata.

## 6. Migration Impact On Existing Scripts

### `scripts/import_chess_fen_label_assist.py`

No core behavior change is required. It already produces review-only drafts. The plan only requires adding/normalizing fields so downstream tools can distinguish AI review signals from human verification:

- `verification_source="ai_review_only"` for AI drafts.
- `human_verified=false`.
- `label_status="needs_manual_fen"`.
- `accepted_for_corpus=false`.

### `scripts/promote_chess_fen_label_draft.py`

Promotion becomes stricter. Some rows that previously promoted will skip with explicit reasons until crop evidence, human verification, and ambiguity resolution are present.

### `scripts/validate_chess_fen_labels.py`

Validation becomes the canonical enforcement point. Existing legacy fixtures may need to add `label_status="verified"`, `human_verified=true`, `verification_source="human_visual"`, crop hashes, and square-diff acknowledgement.

### `scripts/check_chess_fen_profile_ready.py`

No architectural rewrite is needed; it should inherit stricter behavior from the validator and expose clearer next actions.

### `scripts/evaluate_chess_fen_corpus.py`

No architectural rewrite is needed; it should surface the new validation summary fields.

## 7. Tests To Add Or Update

### Validator Tests

- [ ] `ai_approved=true` with valid FEN but `human_verified=false` fails.
- [ ] `arbiter_approved=true` with valid FEN but no human provenance fails.
- [ ] Valid FEN with missing `label_status` fails.
- [ ] Valid FEN with `label_status="accepted"` fails.
- [ ] Valid FEN with `label_status="needs_manual_fen"` fails.
- [ ] Valid FEN with missing `verified_by` fails.
- [ ] Valid FEN with missing `verified_at` fails.
- [ ] Valid FEN with missing crop path fails.
- [ ] Valid FEN with nonexistent crop path fails.
- [ ] Valid FEN with `ai_requires_review=true` fails.
- [ ] Valid FEN with non-empty `ai_ambiguous_squares` fails.
- [ ] Valid manual FEN with `human_verified=true`, `label_status="verified"`, human provenance, existing crop, crop hash, square diff acknowledgement, and clean python-chess status passes.

### Promotion Tests

- [ ] Promotion skips AI-only suggestion even if `ai_approved=true`.
- [ ] Promotion skips row with `ai_requires_review=true`.
- [ ] Promotion skips row with missing crop.
- [ ] Promotion writes `label_status="verified"`, `label_source="manual_fen"`, `verification_source="human_visual"`, `human_verified=true`, `ai_assisted`, `crop_sha256`, and square diff evidence.
- [ ] Deprecated `--accept-ai-suggestions` does not change output and emits/records a warning.

### Python-Chess Validation Tests

- [ ] Parse error becomes blocker.
- [ ] Missing king becomes blocker.
- [ ] Pawns on back rank become blocker.
- [ ] Bad castling rights are warning or blocker according to the finalized policy.
- [ ] Diagram-style FEN with `- - 0 1` and valid kings passes.

### Profile/Corpus Tests

- [ ] Profile readiness rejects any label that is not exactly `verified`.
- [ ] Profile readiness rejects AI-only labels even when candidate confidence is high.
- [ ] Corpus eval reports python-chess blocker counts.
- [ ] Existing PGN tests continue to assert review-only/unsafe source FEN blocks strict diagram-sourced PGN export.

## 8. Acceptance Criteria

- No path can convert `ai_approved`, `arbiter_approved`, high confidence, or `ai_requires_review=false` into `label_status=verified`.
- Every promoted label is human verified per row.
- Every promoted label has an existing crop path and crop hash evidence.
- Every profile/corpus label has `label_status == "verified"`.
- Every profile/corpus label passes project `validate_fen()`.
- Every profile/corpus label passes `python-chess` blocker classification.
- AI ambiguity fields block validation until resolved by human visual review.
- Profile readiness fails loudly and actionably when labels are missing human provenance, crop evidence, square diff acknowledgement, or valid FEN status.
- Existing AI assist remains useful as a prioritization/review aid, but cannot mutate corpus labels.

## 9. Rollback Strategy

- Implement the validator changes first in report/audit mode if existing seed labels need migration.
- Keep generated AI assist drafts readable; do not delete old fields.
- If the stricter validator overblocks known-good labels, add explicit migration metadata rather than relaxing AI-safety rules.
- Roll back profile/corpus hard failure integration before rolling back the audit output.
- Prefer more `needs_review` output over any chance of false verified FEN.

## 10. Risks And Mitigations

- Risk: existing verified fixtures are missing new metadata and fail.  
  Mitigation: provide a migration checklist and update fixtures with human provenance instead of weakening gates.

- Risk: python-chess flags legitimate study positions as abnormal.  
  Mitigation: classify structural impossibilities as blockers and metadata/history concerns as warnings unless explicitly contradicted by source context.

- Risk: manual labeling becomes slower.  
  Mitigation: keep AI/template suggestions as prefill evidence, but require a clear square-diff/crop confirmation step.

- Risk: profile readiness metrics temporarily drop.  
  Mitigation: treat this as honest quality measurement; unsafe labels should not be profile-ready.

- Risk: field names remain confusing.  
  Mitigation: document canonical meanings in `docs/chess-study-data-contracts.md` and keep AI fields under `ai_*` review-only semantics.

