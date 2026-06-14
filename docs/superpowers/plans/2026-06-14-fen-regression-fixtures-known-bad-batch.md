# FEN Regression Fixtures For Known Bad Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add regression fixtures and tests that permanently block known bad chess FEN review failure classes, especially `p010_d002` where a candidate/AI FEN can visually mistake a black rook on `e5` for a black pawn.

**Architecture:** Use synthetic board/FEN fixtures for deterministic CI-safe tests, and treat local real crops as optional evidence. Tests should cover square-level diffs, AI-review-only safety, validator/profile gates, accepted false-positive audit inclusion, and one full verified happy path.

**Tech Stack:** Python `unittest`, existing FEN utilities in `chess_fen_hardening.py`, existing review/import/promote/validate/eval scripts, synthetic PNG board helpers already present in `test_chess_fen_recognition.py`.

---

## 1. Inspection Findings

### Existing protection already present

- `chess_fen_hardening.py` exists and includes:
  - `KNOWN_BAD_EXPECTED_FENS`;
  - `KNOWN_BAD_EXPECTED_FENS["p010_d002"]`;
  - `fen_to_cells()`;
  - `square_level_fen_diff()`;
  - AI-only and human verification source helpers;
  - `has_square_diff_ack()`.
- `scripts/validate_chess_fen_labels.py` already imports `KNOWN_BAD_EXPECTED_FENS` and reports `known_bad_square_mismatch` when `p010_d002` differs on `e5`.
- `scripts/audit_chess_fen_false_positives.py` already exists and scans high-confidence/AI-approved rows plus `KNOWN_BAD_EXPECTED_FENS`.
- `scripts/promote_chess_fen_label_draft.py` already blocks AI-only promotion when `human_verified` is missing and when only `ai_suggested_fen` exists without manual FEN.
- `scripts/import_chess_fen_label_assist.py` already keeps AI results review-only: `fen=""`, `label_status="needs_manual_fen"`, `accepted_for_corpus=false`.

### Existing tests near this area

- `test_chess_fen_recognition.py` already tests:
  - review queue is review-only;
  - AI label assist import creates a manual draft, not verified labels;
  - invalid AI FEN is rejected from `ai_suggested_fen`;
  - promotion requires manual approval;
  - promotion blocks human-accepted AI suggestion without manual FEN;
  - review-only labels fail validation/corpus gate;
  - corpus eval fails on false positives through recognizer eval.

### Local artifact status

- `output/chess_study_html/review/fen_ai_candidate_review_batch.html` exists and contains `p010_d002`.
- `output/chess_study_html/assets/diagrams/p010_d002.png` exists locally.
- `output/chess_study_html/review/AI FEN candidate review batch_files/` is missing in this worktree.
- `reports/chess_fen/review_queue/` is missing in this worktree.
- `reports/chess_fen/label_aids/` is missing in this worktree.

### Important test-design constraint

Do not encode a new real manual FEN from memory. For CI, use synthetic board/FEN fixtures that represent the same failure class. For the real `p010_d002`, use only the existing repo constant `KNOWN_BAD_EXPECTED_FENS["p010_d002"]` or the actual local crop when present.

## 2. Canonical Synthetic Fixture Data

Use this tiny CI-safe pair for the square-level failure class:

```python
EXPECTED_ROOK_E5_FEN = "4k3/8/8/4r3/8/8/8/4K3 w - - 0 1"
CANDIDATE_PAWN_E5_FEN = "4k3/8/8/4p3/8/8/8/4K3 w - - 0 1"
```

Expected square-level diff:

```json
{
  "square": "e5",
  "candidate_piece": "black pawn",
  "manual_piece": "black rook",
  "candidate_fen_char": "p",
  "manual_fen_char": "r",
  "severity": "critical"
}
```

Expected text renderer output:

```text
p010_d002: e5 black rook, not black pawn
```

Note: the current helper returns raw `expected_piece="r"` and `actual_piece="p"`. The implementation phase should either add a renderer/naming wrapper or assert the raw diff plus the renderer output when that helper exists.

## 3. Planned Regression Tests

### Test 1: `test_square_diff_reports_p010_style_rook_not_pawn`

**Fixture data:**

- `record_id = "p010_d002"`
- expected/manual FEN: `EXPECTED_ROOK_E5_FEN`
- candidate/current FEN: `CANDIDATE_PAWN_E5_FEN`

**Expected result:**

- `square_level_fen_diff(EXPECTED_ROOK_E5_FEN, CANDIDATE_PAWN_E5_FEN)` includes exactly one critical mismatch for `e5`.
- Raw diff says expected/manual piece is `r` and candidate/actual piece is `p`.
- Text renderer says `p010_d002: e5 black rook, not black pawn`.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`
- Possibly modify/create helper: `chess_fen_hardening.py`

**Acceptance criteria:**

- The test does not use a real crop.
- The test does not depend on AI.
- The test expresses the exact known failure class.

### Test 2: `test_known_bad_p010_d002_candidate_fails_label_validation`

**Fixture data:**

- `id = "p010_d002"`
- `fen` is a candidate that differs from `KNOWN_BAD_EXPECTED_FENS["p010_d002"]` on `e5`.
- `crop_path` points to a synthetic temporary PNG.
- `verified_by`, `verified_at`, and `label_status="verified"` are present to prove the known-bad diff still blocks even when other fields look valid.

**Expected result:**

- `validate_chess_fen_labels()` returns `status="failed"`.
- Issue codes include `known_bad_square_mismatch`.
- Issue payload includes an `e5` square diff.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`

**Acceptance criteria:**

- A valid-looking verified row cannot bypass the known-bad square mismatch.

### Test 3: `test_ai_approved_valid_fen_is_not_verification`

**Fixture data:**

```json
{
  "id": "ai_valid_but_not_human",
  "crop_path": "<temporary board.png>",
  "ai_suggested_fen": "4k3/8/8/4p3/8/8/8/4K3 w - - 0 1",
  "ai_approved": true,
  "ai_requires_review": false,
  "ai_confidence": 0.99
}
```

**Expected result:**

- `promote_chess_fen_label_draft()` fails.
- `promoted_count == 0`.
- skipped reason is `manual_approval_missing`.
- no output row has `label_status="verified"`.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`

**Acceptance criteria:**

- `ai_approved=True` and a valid AI FEN never imply verification.

### Test 4: `test_high_confidence_candidate_without_verified_status_fails_validator`

**Fixture data:**

```json
{
  "id": "high_confidence_review_only",
  "crop_path": "<temporary board.png>",
  "fen": "4k3/8/8/4r3/8/8/8/4K3 w - - 0 1",
  "candidate_confidence": 0.96,
  "label_status": "needs_manual_fen",
  "verified_by": "",
  "verified_at": ""
}
```

**Expected result:**

- `validate_chess_fen_labels()` fails.
- Issues include `review_only_label_status`, `verified_by_missing`, and `verified_at_missing`.
- If validator hardening is implemented later, issues should also include `human_verified_missing` and/or `label_status_not_verified`.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`

**Acceptance criteria:**

- Confidence does not become a surrogate for `verified`.

### Test 5: `test_false_positive_audit_includes_high_confidence_risky_mismatch`

**Fixture data:**

Use `scripts.audit_chess_fen_false_positives.audit_chess_fen_false_positives()` with JSONL row:

```json
{
  "id": "p010_d002",
  "fen": "6k1/p4p1p/3p1p2/2p1p3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1",
  "confidence": 0.97,
  "ai_approved": true,
  "verification_source": "ai_review_only",
  "label_status": "verified"
}
```

This uses the existing `KNOWN_BAD_EXPECTED_FENS["p010_d002"]` as the expected reference, not a newly invented real FEN.

**Expected result:**

- Audit status is `failed`.
- Findings include:
  - `high_confidence_or_ai_approved_without_human_verification`;
  - `verified_label_has_ai_only_source`;
  - `known_bad_square_mismatch`.
- `known_bad_square_mismatch.square_diffs` contains `e5`.

**Files to modify:**

- Test: `test_chess_fen_pipeline_hardening.py` or `test_chess_fen_recognition.py`
- Possibly modify: `scripts/audit_chess_fen_false_positives.py` if output needs normalized piece names.

**Acceptance criteria:**

- Accepted/high-confidence AI-looking rows enter the false-positive audit even when they are not `requires_review`.

### Test 6: `test_review_only_label_statuses_are_rejected`

**Fixture data:**

Create three rows with the same valid FEN and crop, but these statuses:

- `draft`
- `needs_manual_fen`
- `review_required`

**Expected result:**

- `validate_chess_fen_labels()` fails.
- Each row emits `review_only_label_status`.
- `valid_label_count == 0`.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`

**Acceptance criteria:**

- No review-only status can become corpus/profile input.

### Test 7: `test_full_verified_manual_path_passes_validator`

**Fixture data:**

Use a synthetic valid board/crop and a minimal verified row:

```json
{
  "id": "verified_rook_e5",
  "crop_path": "<temporary board.png>",
  "fen": "4k3/8/8/4r3/8/8/8/4K3 w - - 0 1",
  "verified_by": "unit-test-human",
  "verified_at": "2026-06-14",
  "verification_source": "human_visual",
  "human_verified": true,
  "square_diff_ack": true,
  "label_status": "verified",
  "notes": "synthetic visual fixture"
}
```

If validator hardening requires `crop_sha256`, compute it using `crop_sha256(crop_path)`.

**Expected result:**

- `validate_chess_fen_labels()` passes.
- `valid_label_count == 1`.
- `issue_count == 0`.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`

**Acceptance criteria:**

- The test proves the safe manual path still works, so hardening does not only block.

### Test 8: `test_corpus_gate_fails_false_positive_even_when_accuracy_threshold_passes`

**Fixture data:**

Synthetic labels and templates where enough rows are exact to pass threshold, but one recognized FEN differs from expected.

Practical implementation options:

1. Patch `scripts.evaluate_chess_fen_corpus.evaluate_chess_fen_recognizer()` to return:

```json
{
  "status": "failed",
  "case_count": 20,
  "fen_count": 20,
  "exact_fen_count": 19,
  "exact_fen_accuracy": 0.95,
  "false_positive_count": 1,
  "false_positive_rate": 0.05,
  "square_accuracy": 0.99,
  "cases": [
    {
      "id": "p010_d002",
      "expected_fen": "4k3/8/8/4r3/8/8/8/4K3 w - - 0 1",
      "actual_fen": "4k3/8/8/4p3/8/8/8/4K3 w - - 0 1",
      "false_positive": true
    }
  ]
}
```

2. Or build actual synthetic crops/templates if needed for an integration-level test.

**Expected result:**

- `evaluate_chess_fen_corpus()` returns `status="failed"`.
- `failed_case_count == 1`.
- `total_false_positive_count == 1`.
- `reasons` or case summaries explain false positive despite accuracy above threshold.

**Files to modify:**

- Test: `test_chess_fen_recognition.py`
- Possibly modify: `scripts/evaluate_chess_fen_corpus.py` to surface richer false-positive reasons.

**Acceptance criteria:**

- Accuracy cannot hide false positives.

### Test 9: Optional Real Local Crop Smoke For `p010_d002`

**Fixture data:**

- `output/chess_study_html/assets/diagrams/p010_d002.png`, if present.
- `KNOWN_BAD_EXPECTED_FENS["p010_d002"]`.

**Expected result:**

- If the crop exists, optional diagnostic test/report can verify that the local file is available and linked in review artifacts.
- This test must be skipped when the crop is missing.

**Files to modify:**

- Prefer not to add to required CI tests unless the crop is committed.
- If added, mark as optional/local diagnostic under a separate test or smoke script.

**Acceptance criteria:**

- CI does not depend on local `output/`.
- Local debugging can still reference the actual p010 crop.

## 4. Files To Modify During Implementation

- `test_chess_fen_recognition.py`  
  Main place for synthetic regression tests and integration tests around validator/import/promote/eval.

- `test_chess_fen_pipeline_hardening.py`  
  Good fit for false-positive audit tests if keeping hardening-specific tests separate.

- `chess_fen_hardening.py`  
  Add piece naming/text renderer if not already present.

- `scripts/audit_chess_fen_false_positives.py`  
  Extend normalized output if the accepted audit test needs richer square diff fields.

- `scripts/validate_chess_fen_labels.py`  
  Only if planned validator hardening is implemented at the same time.

- `scripts/evaluate_chess_fen_corpus.py`  
  Only if corpus false-positive failure reporting needs richer detail.

## 5. Acceptance Criteria For This Regression Batch

- The `p010_d002` failure class is expressible as `p010_d002: e5 black rook, not black pawn`.
- The square diff test does not require AI.
- The square diff test does not require a real crop.
- AI approval and high confidence cannot promote a row.
- Review-only statuses are rejected.
- A fully human-verified row passes.
- False positives fail corpus/profile gates even when exact accuracy is otherwise above threshold.
- Accepted/high-confidence false positives are exported by the audit path, not hidden because they lack `requires_review`.
- No test encodes a new real manual FEN from memory.

## 6. Validation Commands

Targeted:

```powershell
python -m unittest test_chess_fen_recognition.py
```

If hardening tests are placed in the separate file:

```powershell
python -m unittest test_chess_fen_pipeline_hardening.py test_chess_fen_recognition.py
```

Static compile for touched modules:

```powershell
python -m py_compile chess_fen_hardening.py scripts/audit_chess_fen_false_positives.py scripts/validate_chess_fen_labels.py scripts/evaluate_chess_fen_corpus.py
```

## 7. Risks And Mitigations

- Risk: tests accidentally depend on local `output/` crops.  
  Mitigation: use synthetic FEN/crop fixtures for required tests; keep real p010 crop checks optional.

- Risk: fixture FENs are valid but not visually tied to crop.  
  Mitigation: square-diff tests compare FEN-to-FEN only; visual tests should use generated synthetic board images.

- Risk: too many tests duplicate existing safety checks.  
  Mitigation: add only missing assertions: known-bad `e5`, high-confidence audit inclusion, and explicit corpus false-positive gate.

- Risk: current helper reports raw `r/p` rather than "black rook/pawn".  
  Mitigation: add a small naming/text renderer test as the first failing unit, then implement the renderer in `chess_fen_hardening.py`.

## 8. Self-Review Checklist

- Plan includes all seven requested regression classes.
- Plan respects "do not encode full manual FEN from memory".
- Plan uses synthetic fixtures for CI safety.
- Plan references existing repo files and current hardening utilities.
- Plan includes test names, fixture data, expected results, files to modify, and acceptance criteria.

