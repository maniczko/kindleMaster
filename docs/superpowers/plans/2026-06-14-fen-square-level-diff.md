# FEN Square-Level Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide deterministic square-level FEN diff tooling so every candidate/manual/verified comparison can say exactly which chess square differs, for example: `p010_d002: e5 black rook, not black pawn`.

**Architecture:** Extend the existing `chess_fen_hardening.py` helper into the canonical FEN placement diff utility, then reuse it in review queues, manual verification drafts, AI import reports, evaluator cases, and accepted false-positive audit output. Keep the implementation pure Python and independent from AI.

**Tech Stack:** Python standard library, existing FEN parser/validator helpers, JSONL reports, static HTML rendering, existing `unittest` suite.

---

## 1. Current State Findings With File References

### Existing reusable foundation

- `chess_fen_hardening.py:40` already defines `fen_to_cells(fen_or_placement)`, which parses a six-field FEN or placement string into 64 cells.
- `chess_fen_hardening.py:70` already defines `square_level_fen_diff(expected_fen, actual_fen)`.
- `chess_fen_hardening.py:85` currently returns a minimal diff shape: `square`, `expected_piece`, `actual_piece`, and `reason`.
- `test_chess_fen_pipeline_hardening.py:16` already tests the known `p010_d002` mismatch and expects `e5` to differ.

Finding: do not create a second parser. Expand `chess_fen_hardening.py` into the canonical helper and update the old minimal output through backwards-compatible keys.

### Existing evaluator has private duplicate placement parsing

- `scripts/evaluate_chess_fen_recognizer.py:175` defines `_score_placement()`.
- `scripts/evaluate_chess_fen_recognizer.py:183` and `scripts/evaluate_chess_fen_recognizer.py:184` parse expected/actual placement.
- `scripts/evaluate_chess_fen_recognizer.py:195` records confusion counts.
- `scripts/evaluate_chess_fen_recognizer.py:204` defines a private `_placement_to_cells()`.

Finding: evaluator currently computes aggregate square accuracy and confusion, but it does not export human-readable square-level differences. It should call the shared helper and include diffs per case.

### Existing validation uses square diff only for known-bad fixture

- `scripts/validate_chess_fen_labels.py:21` imports `square_level_fen_diff`.
- `scripts/validate_chess_fen_labels.py:91` compares `KNOWN_BAD_EXPECTED_FENS[record_id]` with the record FEN.
- `scripts/validate_chess_fen_labels.py:93` includes `square_diffs` for `known_bad_square_mismatch`.
- `scripts/validate_chess_fen_labels.py:133` requires square-diff acknowledgement before promoted labels pass validation.

Finding: validation already has a square-diff hook, but only for known-bad labels. It should use richer diff output when candidate/manual fields are present.

### Existing AI/manual workflow has candidate fields but no rendered diff

- `scripts/export_chess_fen_review_queue.py:179` exports `candidate_fen`.
- `scripts/export_chess_fen_review_queue.py:180` exports `candidate_placement`.
- `scripts/export_chess_fen_review_queue.py:458` exports `deterministic_suggested_fen`.
- `scripts/export_chess_fen_review_queue.py:461` exports `original_candidate_fen`.
- `scripts/import_chess_fen_label_assist.py:75` exports `ai_suggested_fen`.
- `scripts/promote_chess_fen_label_draft.py:112` promotes only `fen` or `manual_fen`.
- `scripts/promote_chess_fen_label_draft.py:141` sets `square_diff_ack=True`.
- `scripts/promote_chess_fen_label_draft.py:142` passes through `square_diff` if present.

Finding: the workflow has enough fields to compare candidate/manual/AI/deterministic FENs, but no standard diff renderer. The review UI is making humans compare full FEN strings in their heads. Nasty little goblin of a UX bug.

### Runtime result already has per-square recognizer data

- `chess_position_recognizer.py:105` defines `ChessFenResult.squares`.
- `chess_position_recognizer.py:128` serializes `squares`.
- `chess_position_recognizer.py:856` creates square records with `square`, `piece`, and `confidence`.
- `test_chess_fen_recognition.py:2568` verifies the image-template result exposes a 64-square confidence matrix.

Finding: future diff cards can combine FEN-to-FEN diffs with recognizer square confidence, but v1 should not require square confidence to compute diff.

## 2. Proposed Utility API

Extend `chess_fen_hardening.py`.

### Types

Use `TypedDict` for clarity without adding dependencies:

```python
class SquareDiff(TypedDict):
    square: str
    candidate_piece: str
    manual_piece: str
    candidate_fen_char: str
    manual_fen_char: str
    severity: str
    reason: str
```

For compatibility, optionally include old aliases:

```python
expected_piece: str
actual_piece: str
```

### Constants

```python
FEN_SQUARES = [f"{file}{rank}" for rank in range(8, 0, -1) for file in "abcdefgh"]

PIECE_NAMES = {
    "P": "white pawn",
    "N": "white knight",
    "B": "white bishop",
    "R": "white rook",
    "Q": "white queen",
    "K": "white king",
    "p": "black pawn",
    "n": "black knight",
    "b": "black bishop",
    "r": "black rook",
    "q": "black queen",
    "k": "black king",
    "": "empty square",
    "empty": "empty square",
}
```

### Functions

```python
def fen_placement_to_square_map(fen_or_placement: str) -> dict[str, str]:
    """Return {'a8': 'r', ..., 'h1': 'R'} using '' for empty squares."""
```

```python
def piece_name(piece: str) -> str:
    """Return human-readable piece names such as 'black rook'."""
```

```python
def compare_fen_placements(candidate_fen: str, expected_fen: str) -> list[SquareDiff]:
    """Compare candidate/current FEN to expected/manual/verified FEN."""
```

Parameter naming:

- `candidate_fen`: model/recognizer/AI/current output.
- `expected_fen`: manual/corrected/verified/reference output.

Output naming:

- `candidate_piece`: human readable, e.g. `black pawn`.
- `manual_piece`: human readable, e.g. `black rook`.
- `candidate_fen_char`: raw char, e.g. `p`.
- `manual_fen_char`: raw char, e.g. `r`.

### Severity rules

- `critical`: king mismatch, piece-vs-piece mismatch involving queen/rook/king, known-bad record context, or candidate places a piece where manual has a different major/minor piece.
- `high`: piece-vs-piece mismatch involving bishop/knight/pawn.
- `medium`: empty-vs-piece mismatch.
- `low`: side-to-move-only difference or metadata-only difference.

For v1, severity is deterministic and local; do not call AI.

## 3. Renderers

### Text renderer

```python
def render_square_diff_text(record_id: str, diffs: list[SquareDiff]) -> list[str]:
    ...
```

Target exact phrasing:

```text
p010_d002: e5 black rook, not black pawn
```

Rule:

- Phrase as manual/expected truth first, candidate second.
- For empty square:
  - manual piece, candidate empty: `p010_d002: e5 black rook, not empty square`
  - manual empty, candidate piece: `p010_d002: e5 empty square, not black pawn`

### JSON renderer

```python
def render_square_diff_json(record_id: str, diffs: list[SquareDiff]) -> dict[str, Any]:
    return {"id": record_id, "diffs": diffs}
```

Target output:

```json
{
  "id": "p010_d002",
  "diffs": [
    {
      "square": "e5",
      "candidate_piece": "black pawn",
      "manual_piece": "black rook",
      "candidate_fen_char": "p",
      "manual_fen_char": "r",
      "severity": "critical",
      "reason": "piece_mismatch"
    }
  ]
}
```

### HTML renderer

```python
def render_square_diff_html(record_id: str, diffs: list[SquareDiff]) -> str:
    ...
```

HTML card fragment:

```html
<table class="fen-square-diff">
  <thead>
    <tr>
      <th>Square</th>
      <th>Manual / expected</th>
      <th>Candidate</th>
      <th>Severity</th>
      <th>Reason</th>
    </tr>
  </thead>
  <tbody>
    <tr class="severity-critical">
      <td>e5</td>
      <td>black rook <code>r</code></td>
      <td>black pawn <code>p</code></td>
      <td>critical</td>
      <td>piece_mismatch</td>
    </tr>
  </tbody>
</table>
```

## 4. Side-To-Move Differences

`compare_fen_placements()` compares placement only. Add optional full-FEN compare:

```python
def compare_fen(candidate_fen: str, expected_fen: str) -> dict[str, Any]:
    return {
        "placement_diffs": [...],
        "side_to_move_diff": {"candidate": "w", "manual": "b", "severity": "low"} | None,
        "metadata_diffs": [...]
    }
```

Rules:

- Side-to-move-only difference should not produce square diffs.
- It should produce a separate `side_to_move_diff`.
- Severity is `low` unless the calling audit treats side-to-move as a blocking condition.

## 5. Invalid FEN Handling

Diff functions must be deterministic and explicit:

- Invalid placement raises `ValueError` in low-level parser.
- Public compare function catches parser errors and returns:

```json
{
  "placement_diffs": [],
  "errors": [
    {
      "field": "candidate_fen",
      "code": "invalid_fen_placement",
      "message": "FEN placement must have 8 ranks"
    }
  ]
}
```

Do not silently return an empty diff for invalid FEN. Empty diff means exact match.

## 6. Integration Points

### Review queue

File:

- `scripts/export_chess_fen_review_queue.py`

Plan:

- When both `original_candidate_fen` and `deterministic_suggested_fen` exist, attach:
  - `square_diff`
  - `square_diff_text`
  - `square_diff_summary`
- In `manual_review_sheet.html`, show square diff table above the manual FEN input instructions.

### Manual verification draft

File:

- `scripts/export_chess_fen_review_queue.py`

Plan:

- Draft rows should include an empty `manual_fen` field.
- When a candidate exists but no manual FEN exists, include a `candidate_square_map` or rendered board table to support review.
- Once manual FEN is entered externally, promotion can compute candidate-vs-manual diffs.

### AI import

File:

- `scripts/import_chess_fen_label_assist.py`

Plan:

- If both source candidate and `ai_suggested_fen` exist, attach `candidate_vs_ai_square_diff`.
- Do not interpret AI diff as correctness.
- Use it for triage only:
  - no diff: AI agrees with candidate
  - many diffs: AI conflicts with candidate
  - key piece diffs: high review priority

### Promotion

File:

- `scripts/promote_chess_fen_label_draft.py`

Plan:

- When promoting a human-verified manual FEN, compute:
  - deterministic candidate vs manual FEN diff when `deterministic_suggested_fen` exists.
  - AI candidate vs manual FEN diff when `ai_suggested_fen` exists.
- Preserve the diff in promoted labels under:

```json
{
  "square_diff": [...],
  "square_diff_ack": true
}
```

- Keep current rule: missing `square_diff_ack` blocks promotion.

### Label validation

File:

- `scripts/validate_chess_fen_labels.py`

Plan:

- Continue using known-bad expected FEN for fixtures like `p010_d002`.
- When `square_diff` exists, validate shape:
  - square in `a1..h8`
  - piece names match FEN chars
  - severity in allowed values
  - reason in allowed values
- For new labels, require `square_diff_ack=True`.

### Evaluator reports

File:

- `scripts/evaluate_chess_fen_recognizer.py`

Plan:

- Replace private `_placement_to_cells()` or delegate it to `chess_fen_hardening.fen_placement_to_square_map()`.
- Each `cases[]` entry should include:
  - `square_diffs`
  - `square_diff_text`
  - existing `square_accuracy`
  - existing confusion metrics
- Preserve aggregate metrics.

### Accepted false-positive audit

Planned file:

- `scripts/export_chess_fen_accepted_audit.py`

Plan:

- Use `compare_fen_placements()` whenever expected/manual/candidate FEN pairs are available.
- Known `p010_d002` should render:

```text
p010_d002: e5 black rook, not black pawn
```

- Risk scoring should consume `severity` from `SquareDiff`.

## 7. Tests

Create or extend:

- `test_chess_fen_square_diff.py`
- optionally extend `test_chess_fen_pipeline_hardening.py`

### Required tests

- [ ] `test_pawn_vs_rook_on_e5`

```python
candidate = "6k1/p4p1p/3p1p2/2p1p3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"
manual = "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"
diffs = compare_fen_placements(candidate, manual)
assert diffs == [{
    "square": "e5",
    "candidate_piece": "black pawn",
    "manual_piece": "black rook",
    "candidate_fen_char": "p",
    "manual_fen_char": "r",
    "severity": "critical",
    "reason": "piece_mismatch",
}]
assert render_square_diff_text("p010_d002", diffs) == ["p010_d002: e5 black rook, not black pawn"]
```

- [ ] `test_empty_vs_piece`

Candidate empty at `e5`, manual black rook at `e5`.

Expected:

```text
p010_d002: e5 black rook, not empty square
```

- [ ] `test_piece_vs_empty`

Candidate black pawn at `e5`, manual empty at `e5`.

Expected:

```text
p010_d002: e5 empty square, not black pawn
```

- [ ] `test_side_to_move_only_difference`

Candidate and manual placement identical, active color differs.

Expected:

- `compare_fen_placements()` returns `[]`.
- `compare_fen()` returns `side_to_move_diff`.

- [ ] `test_invalid_candidate_fen_handling`

Candidate has 7 ranks.

Expected:

- low-level parser raises `ValueError`.
- public compare wrapper returns error object.

- [ ] `test_invalid_manual_fen_handling`

Manual/expected FEN invalid.

Expected: error references `expected_fen` or `manual_fen`, not candidate.

- [ ] `test_64_square_exact_match`

Candidate and manual identical.

Expected:

- no placement diffs
- no side-to-move diff
- no errors

- [ ] `test_piece_name_helper_all_pieces`

Assert all 12 piece symbols plus empty render correctly.

- [ ] `test_html_renderer_escapes_values`

Use malicious record id or reason string.

Expected: HTML is escaped.

## 8. Acceptance Criteria

- The plan’s implementation can express the known error exactly as:

```text
p010_d002: e5 black rook, not black pawn
```

- Diff requires no AI.
- Diff is deterministic.
- Diff works for:
  - candidate vs manual,
  - candidate vs verified,
  - recognizer actual vs expected.
- `compare_fen_placements()` returns square-level diffs with:
  - square,
  - candidate human name,
  - manual/expected human name,
  - raw FEN chars,
  - severity,
  - reason.
- Side-to-move-only differences are represented separately from square diffs.
- Invalid FEN handling is explicit and cannot be mistaken for an exact match.
- Existing evaluator aggregate metrics remain intact.
- Existing hardening tests for `p010_d002` still pass.

## 9. Files Likely To Modify During Implementation

Create:

- `test_chess_fen_square_diff.py`

Modify:

- `chess_fen_hardening.py`
- `scripts/export_chess_fen_review_queue.py`
- `scripts/import_chess_fen_label_assist.py`
- `scripts/promote_chess_fen_label_draft.py`
- `scripts/validate_chess_fen_labels.py`
- `scripts/evaluate_chess_fen_recognizer.py`
- future `scripts/export_chess_fen_accepted_audit.py` after that tool exists
- `docs/chess-study-data-contracts.md`

Do not modify:

- runtime accepted/publish logic in `pymupdf_chess_extractor.py` for this utility-only change.
- OpenAI provider behavior.
- PGN strict export logic.

## 10. Suggested Implementation Tasks

### Task 1: Expand canonical diff helper

**Files:**
- Modify: `chess_fen_hardening.py`
- Create: `test_chess_fen_square_diff.py`

- [ ] Add `PIECE_NAMES`.
- [ ] Add `fen_placement_to_square_map()`.
- [ ] Add `piece_name()`.
- [ ] Add `compare_fen_placements()`.
- [ ] Keep `square_level_fen_diff()` as backwards-compatible wrapper.
- [ ] Add tests for pawn-vs-rook, empty-vs-piece, piece-vs-empty, exact match, and invalid placement.

### Task 2: Add renderers

**Files:**
- Modify: `chess_fen_hardening.py`
- Test: `test_chess_fen_square_diff.py`

- [ ] Add text renderer.
- [ ] Add JSON renderer.
- [ ] Add HTML renderer.
- [ ] Add escaping tests.

### Task 3: Add full-FEN compare

**Files:**
- Modify: `chess_fen_hardening.py`
- Test: `test_chess_fen_square_diff.py`

- [ ] Add `compare_fen()`.
- [ ] Return side-to-move diff separately.
- [ ] Return metadata diffs separately for castling/en-passant/clocks.
- [ ] Add side-to-move-only test.

### Task 4: Integrate evaluator cases

**Files:**
- Modify: `scripts/evaluate_chess_fen_recognizer.py`
- Test: `test_chess_fen_recognition.py` or `test_chess_fen_square_diff.py`

- [ ] Replace or delegate private `_placement_to_cells()`.
- [ ] Add `square_diffs` and `square_diff_text` to each case.
- [ ] Preserve `square_accuracy`, `confusion`, and existing summary fields.

### Task 5: Integrate review and promotion artifacts

**Files:**
- Modify: `scripts/export_chess_fen_review_queue.py`
- Modify: `scripts/import_chess_fen_label_assist.py`
- Modify: `scripts/promote_chess_fen_label_draft.py`
- Modify: `scripts/validate_chess_fen_labels.py`

- [ ] Add candidate-vs-deterministic and candidate-vs-AI diffs where both FENs exist.
- [ ] Preserve candidate-vs-manual diffs during promotion.
- [ ] Validate square diff schema.
- [ ] Keep all AI-derived diffs review-only.

## 11. Validation Commands For Implementation Phase

Run after implementation, not for this planning-only change:

```powershell
python -m py_compile chess_fen_hardening.py scripts\export_chess_fen_review_queue.py scripts\import_chess_fen_label_assist.py scripts\promote_chess_fen_label_draft.py scripts\validate_chess_fen_labels.py scripts\evaluate_chess_fen_recognizer.py
python -m unittest test_chess_fen_square_diff.py test_chess_fen_pipeline_hardening.py test_chess_fen_recognition.py
python kindlemaster.py test --suite quick
```

## 12. Risks And Mitigations

- Risk: breaking existing minimal `square_level_fen_diff()` output.
  - Mitigation: keep old keys or provide wrapper compatibility.

- Risk: candidate/manual parameter confusion reverses output wording.
  - Mitigation: use explicit parameter names and tests requiring `manual first, candidate second` text.

- Risk: invalid FEN becomes “no diffs”.
  - Mitigation: public compare returns errors; low-level parser raises.

- Risk: HTML renderer becomes a mini UI framework.
  - Mitigation: render a small escaped table fragment only.

- Risk: duplicate FEN parsing remains in evaluator.
  - Mitigation: delegate evaluator parsing to shared helper after tests are in place.

- Risk: side-to-move diff gets mixed with square diff.
  - Mitigation: separate `placement_diffs` from `side_to_move_diff`.
