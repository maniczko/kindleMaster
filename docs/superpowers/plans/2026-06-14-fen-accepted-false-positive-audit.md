# FEN Accepted False-Positive Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an audit queue that reviews not only failed/review FEN cases, but also accepted, high-confidence, AI-approved, arbiter-approved, and other high-risk candidates that could be visually wrong.

**Architecture:** Add `scripts/export_chess_fen_accepted_audit.py` as an audit-only tool that reads existing smoke/corpus reports and crop artifacts, scores risk deterministically, exports JSON/JSONL/HTML review queues, and copies crop/grid overlay evidence. Keep publication and strict FEN/PGN export unchanged.

**Tech Stack:** Python standard library, existing JSONL/JSON report shapes, Pillow for crop/grid overlays, existing `chess_position_recognizer.py` helpers, existing `unittest` suite.

---

## 1. Current State Findings With File References

### Existing review queue focuses on `requires_review`

- `scripts/export_chess_fen_review_queue.py:24` exports unresolved scanned-board FEN cases for manual/OpenAI review.
- `scripts/export_chess_fen_review_queue.py:48` builds `review_records` only from `record.get("requires_review")`.
- `scripts/export_chess_fen_review_queue.py:158` constructs `candidate_fen` from placement for review rows.
- `scripts/export_chess_fen_review_queue.py:179` stores `candidate_fen` only if syntactically valid.
- `scripts/export_chess_fen_review_queue.py:446` creates manual verification drafts from selected review records.

Finding: the current queue is safe for unresolved cases but misses the dangerous class where a candidate is accepted/high-confidence and therefore does not enter manual review.

### Runtime recognition carries risk signals

- `chess_position_recognizer.py:105` defines `ChessFenResult` with `fen`, `placement`, `confidence`, `warnings`, `requires_review`, `method`, `bbox`, `board_detected`, and per-square details.
- `chess_position_recognizer.py:197` defines `validate_fen()`, but syntactic validity does not prove visual correctness.
- `pymupdf_chess_extractor.py:1495` defines `_chess_fen_record()`, which serializes `ChessFenResult` plus `page_num`, `page_label`, `filename`, and `source`.
- `pymupdf_chess_extractor.py:2058` writes scanned chess FEN records with page, filename, source, FEN payload, confidence, warnings, and review status.
- `pymupdf_chess_extractor.py:2467` chooses the strongest deterministic result across raw and reader crops.
- `pymupdf_chess_extractor.py:2796` recalibrates cached recognitions and treats warnings such as `side_to_move_inferred`, `dense_board_area_crop_used`, `final_rendered_crop_fen_used`, `reader_visible_crop_fen_used`, and `verified_exact_crop_label_used` as disqualifying for direct cache reuse.
- `pymupdf_chess_extractor.py:2903` publishes exact-crop verified labels only when crop SHA matches a verified label.
- `pymupdf_chess_extractor.py:3033` allows reader visible crop publication only through explicit safety checks.
- `pymupdf_chess_extractor.py:3073` allows sparse exact consensus only when raw and reader crops agree.
- `pymupdf_chess_extractor.py:4528` emits empty review payloads when the board was not deterministically recognized.
- `pymupdf_chess_extractor.py:4552` replaces `side_to_move_inferred` with `side_to_move_marker_detected` when an explicit marker is found.

Finding: many good risk signals already exist. The accepted audit should not invent new recognition logic; it should aggregate these signals and force visual review for risky accepted records.

### Existing evaluator detects false positives only on labeled crops

- `scripts/evaluate_chess_fen_recognizer.py:28` evaluates deterministic recognition against verified labels.
- `scripts/evaluate_chess_fen_recognizer.py:77` marks exact FEN matches.
- `scripts/evaluate_chess_fen_recognizer.py:78` marks false positives when actual FEN exists and differs from expected FEN.
- `scripts/evaluate_chess_fen_recognizer.py:100` records case-level square accuracy.
- `scripts/evaluate_chess_fen_recognizer.py:121` reports `false_positive_count`.
- `scripts/evaluate_chess_fen_recognizer.py:130` reports piece confusion.
- `scripts/evaluate_chess_fen_recognizer.py:144` reports board diagnostics including grid confidence.
- `scripts/evaluate_chess_fen_recognizer.py:175` scores placement cell-by-cell.

Finding: evaluator is strong after labels exist, but it cannot catch false positives in unlabeled accepted runtime output. The new audit queue fills that pre-label review gap.

### Corpus and smoke quality gates are aggregate-level

- `scripts/evaluate_chess_fen_corpus.py:22` evaluates all manifest-declared chess FEN seed datasets.
- `scripts/evaluate_chess_fen_corpus.py:147` accumulates false-positive counts across profiles.
- `scripts/evaluate_chess_fen_corpus.py:190` fails profiles on label/accuracy/false-positive gate failures.
- `test_smoke_chess_quality.py:133` tests FEN acceptance coverage threshold.
- `test_smoke_chess_quality.py:161` tests smoke summary participation.

Finding: smoke/corpus gates can report poor coverage or labeled false positives, but they do not produce a manual review queue for accepted/high-confidence runtime candidates.

### Existing tests include risk scenarios but not accepted audit export

- `test_chess_fen_recognition.py:3831` verifies shifted board recovery can promote a candidate.
- `test_chess_fen_recognition.py:3979` includes a sparse high-confidence false-positive scenario and ensures expanded recovery chooses the dense full board.
- `test_chess_fen_recognition.py:4039` verifies reader-prepared FEN preference over raw review.
- `test_chess_fen_recognition.py:4271` verifies scanned candidate review payload does not invent FEN.
- `test_chess_fen_pipeline_hardening.py` blocks known-bad `p010_d002` and AI-only promotion paths.

Finding: the test suite has useful fixture patterns for risk scoring, but no dedicated tests for exporting accepted/high-risk audit queues.

## 2. Proposed Tool

Create:

```text
scripts/export_chess_fen_accepted_audit.py
```

Purpose:

- Export every `requires_review` record.
- Export a deterministic sample of accepted/high-confidence records.
- Export all accepted records with high-risk warning/method/profile patterns.
- Export all records with known dangerous confusion patterns, including `p010_d002`.
- Produce human-reviewable evidence without mutating labels, EPUB, HTML, PGN, corpus, or runtime publication.

Recommended CLI:

```powershell
python scripts/export_chess_fen_accepted_audit.py reports/smoke/smoke_full.json --output-dir reports/chess_fen/accepted_audit/latest --max-accepted-sample 64 --sample-rate 0.10 --high-confidence-threshold 0.90 --low-grid-threshold 0.55
```

Inputs:

- Smoke report JSON with `cases[].quality_report.chess_fen.records` or `cases[].quality.chess_fen.records`.
- Optional corpus eval JSON from `scripts/evaluate_chess_fen_corpus.py`.
- Optional recognizer eval JSON from `scripts/evaluate_chess_fen_recognizer.py`.
- Optional crop source directories, matching the existing pattern in `export_chess_fen_review_queue.py`.

Outputs:

- `accepted_audit_queue.json`
- `accepted_audit_queue.jsonl`
- `accepted_audit_review.html`
- `accepted_audit_summary.json`
- `crops/*`
- `overlays/*` when crop image is available

The tool must return `0` when export succeeds, even when findings exist. It is an audit queue generator, not a hard failure gate. A future CI mode can add `--fail-on-critical-risk-count`.

## 3. Candidate Inclusion Rules

### Always include

1. Every record with `requires_review=True`.
2. Every record with known-bad id or known-bad square mismatch pattern.
3. Every accepted record with any critical/high-risk warning.
4. Every accepted record from a new/low-corpus profile.
5. Every accepted record with `ai_approved=True`, `arbiter_approved=True`, or `review_opinion="supports_candidate"` if it has not been human verified.
6. Every accepted record whose side to move was inferred instead of explicitly detected.
7. Every accepted record whose method or warnings indicate crop recovery, dense crop fallback, sparse consensus, reader-visible crop rescue, final rendered crop rescue, or verified exact-crop label use.

### Deterministic sample of accepted records

Accepted records without high-risk flags should still be sampled because false positives can be silent.

Rules:

- Compute a stable sample key:

```text
sha256(f"{case_id}|{page}|{filename}|{fen}|{source}")
```

- Include if:
  - integer hash percentile is below `sample_rate`, or
  - record is in the top `max_accepted_sample` by confidence bucket stratification.

Stratification:

- Bucket by confidence:
  - `0.95-1.00`
  - `0.90-0.949`
  - `0.85-0.899`
  - below configured FEN threshold but somehow accepted
- Bucket by method:
  - `image-template-board`
  - `font-board`
  - `verified-exact-crop-label`
  - `reader_visible_crop_fen_used`
  - `final_rendered_crop_fen_used`
  - `sparse_exact_crop_consensus`
  - recovery/expanded/shifted method
- Bucket by source:
  - scanned-page board crop
  - notation layout crop
  - embedded page image
  - font board

Selection order inside each bucket:

1. Higher risk score.
2. Lower confidence, because near-threshold accepted records are fragile.
3. Earlier page.
4. Filename lexical order.

### Low-corpus profile inclusion

Mark profile as low-corpus when any of these are true:

- `profile_seed_label_count < 50`
- `profile_exact_fen_accuracy < 0.95`
- `profile_false_positive_count > 0`
- profile is missing from corpus eval output
- profile/eval date is missing or older than a configured freshness window

Accepted records from low-corpus profiles are included regardless of sample rate.

## 4. Risk Scoring

Each record receives:

```json
{
  "risk_score": 0,
  "risk_level": "low|medium|high|critical",
  "risk_reasons": []
}
```

Suggested score weights:

### Critical

- `+100`: known-bad record id such as `p010_d002`.
- `+90`: accepted/high-confidence candidate conflicts with manual/expected FEN when expected FEN is available.
- `+80`: accepted but has `white_king_count_invalid`, `black_king_count_invalid`, `rank_width_invalid`, or `placement_contains_invalid_piece`.
- `+75`: `ai_approved=True` or `arbiter_approved=True` and no human verification evidence.

### High

- `+45`: `side_to_move_inferred` warning remains.
- `+45`: `dense_board_area_crop_used`.
- `+40`: `reader_visible_crop_fen_used`.
- `+40`: `final_rendered_crop_fen_used`.
- `+40`: `sparse_exact_crop_consensus`.
- `+35`: method contains `shift-recovered`, `border-expanded`, `border-refined`, or `bbox_recovered`.
- `+35`: crop/grid diagnostics below threshold.
- `+30`: profile is new, missing, or low-corpus.
- `+30`: accepted FEN from sparse position with piece count <= 8.

### Medium

- `+20`: confidence below `high_confidence_threshold`.
- `+20`: confidence is exactly/near configured threshold within `0.015`.
- `+20`: no crop available for audit card.
- `+20`: no per-square details available.
- `+15`: side-to-move changed by marker application.
- `+15`: method/source mismatch, e.g. scanned crop with font-board method.

### Piece confusion risk

Apply these when square comparison evidence exists through labels, eval cases, AI/manual FEN, or previous candidate:

- `+35`: queen/rook substitution (`Q<->R`, `q<->r`).
- `+30`: rook/bishop substitution.
- `+30`: knight/bishop substitution.
- `+25`: pawn/piece substitution, including the known `p` vs `r` class from `p010_d002`.
- `+25`: empty/piece substitution on central files/ranks.
- `+10`: empty/piece substitution elsewhere.

Risk level:

- `critical`: score >= 80 or known-bad/invalid accepted condition.
- `high`: score >= 45.
- `medium`: score >= 20.
- `low`: score < 20.

## 5. HTML Review Card Fields

Each `accepted_audit_review.html` card should show:

- Record id.
- Case id/source report.
- Page number and page label.
- Filename and source.
- Current state:
  - `requires_review`
  - `accepted`
  - `accepted_high_risk`
  - `sampled_accepted`
- Risk score, risk level, and risk reasons.
- FEN as `<code>`.
- Placement rendered as an 8x8 text/table view.
- Side to move and whether it was explicit or inferred.
- Confidence and configured thresholds.
- Method.
- Warnings.
- Profile/template metadata when available:
  - profile id
  - seed count
  - exact FEN accuracy
  - false-positive count
  - corpus status
- Crop image.
- Grid overlay image.
- Optional square diff table:
  - square
  - expected/manual piece
  - actual/current piece
  - reason
- Review controls as static fields for manual export:
  - `manual_label`: `correct_fen|false_positive|wrong_piece|wrong_side_to_move|bad_crop|uncertain`
  - `manual_fen`
  - `manual_notes`

No JavaScript is required for v1. If JS is added later, it must only help filtering/copying and must not change labels.

## 6. Integration With Existing Reports

### Smoke report integration

The tool should read:

```text
cases[].quality_report.chess_fen.records
cases[].quality.chess_fen.records
```

It should preserve:

- case id
- output EPUB path
- source PDF path if present
- `diagram_count`
- `fen_count`
- `manual_review_count`

It should not change `_evaluate_chess_fen_acceptance_gate()` in `scripts/run_smoke_tests.py` in v1. Instead, add audit queue generation as an optional post-smoke command.

### Corpus/eval integration

The tool should optionally read corpus/eval summaries:

- `scripts/evaluate_chess_fen_corpus.py` output:
  - profile id
  - seed label count
  - exact accuracy
  - false positive count
  - status
- `scripts/evaluate_chess_fen_recognizer.py` output:
  - case-level expected/actual FEN
  - square accuracy
  - confusion
  - board diagnostics

If these files are absent, the tool should set risk reason:

```text
profile_eval_missing
```

and include accepted records from that profile according to the low-corpus rules.

### Crop copying and overlays

Reuse patterns from `scripts/export_chess_fen_review_queue.py`:

- copy crops from EPUB images when `source_epub` exists;
- fallback to `--crop-source-dir`;
- preserve missing crop count;
- create `overlays/<id>_grid.png` when crop exists.

Overlay should:

- draw 8x8 grid;
- label files/ranks;
- print record id and confidence at top;
- not alter the original crop.

## 7. Data Shapes

### Queue item

```json
{
  "id": "p010_d002",
  "case_id": "fundamenty",
  "page": 10,
  "page_label": 10,
  "filename": "scan_chess_p010_02.png",
  "source": "scanned-page-board-crop",
  "crop_path": "crops/scan_chess_p010_02.png",
  "overlay_path": "overlays/p010_d002_grid.png",
  "fen": "6k1/p4p1p/3p1p2/2p1p3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1",
  "placement": "6k1/p4p1p/3p1p2/2p1p3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1",
  "confidence": 0.94,
  "requires_review": false,
  "method": "image-template-board",
  "warnings": ["side_to_move_inferred"],
  "audit_category": "accepted_high_risk",
  "risk_score": 100,
  "risk_level": "critical",
  "risk_reasons": ["known_bad_p010_d002", "piece_confusion_pawn_vs_rook_e5", "side_to_move_inferred"],
  "square_diffs": [
    {
      "square": "e5",
      "expected_piece": "r",
      "actual_piece": "p",
      "reason": "known_bad_expected_fen"
    }
  ],
  "profile": {
    "id": "fundamenty_merida_like",
    "status": "missing_eval",
    "seed_label_count": 0,
    "exact_fen_accuracy": null,
    "false_positive_count": null
  }
}
```

### Summary

```json
{
  "status": "ok",
  "source_report": "reports/smoke/smoke_full.json",
  "case_count": 1,
  "record_count": 274,
  "requires_review_count": 274,
  "accepted_count": 0,
  "accepted_sampled_count": 0,
  "accepted_high_risk_count": 0,
  "critical_risk_count": 1,
  "high_risk_count": 0,
  "medium_risk_count": 0,
  "missing_crop_count": 0,
  "risk_reason_counts": {
    "known_bad_p010_d002": 1
  },
  "outputs": {
    "queue_json": "accepted_audit_queue.json",
    "queue_jsonl": "accepted_audit_queue.jsonl",
    "review_html": "accepted_audit_review.html"
  }
}
```

## 8. Tests Using Synthetic Records

Create:

```text
test_chess_fen_accepted_audit.py
```

### Test cases

- [ ] `test_requires_review_records_are_always_exported`
  - Build a synthetic smoke report with one `requires_review=True` record.
  - Expected: queue includes it with `audit_category="requires_review"`.

- [ ] `test_high_confidence_accepted_record_is_sampled_deterministically`
  - Build two accepted records with stable ids and high confidence.
  - Run exporter twice with the same sample config.
  - Expected: identical ids in identical order.

- [ ] `test_high_risk_warning_forces_inclusion_without_sampling`
  - Record: `requires_review=False`, `fen` present, warning `side_to_move_inferred`.
  - Expected: included with `risk_reasons` containing `side_to_move_inferred`.

- [ ] `test_known_bad_p010_d002_is_critical`
  - Record: id/filename/page matching `p010_d002`, FEN has pawn on e5.
  - Expected: included, `risk_level="critical"`, square diff contains `e5`.

- [ ] `test_arbiter_approved_without_human_verification_is_high_risk`
  - Record: `arbiter_approved=True`, accepted FEN.
  - Expected: included with `arbiter_approved_without_human_verification`.

- [ ] `test_ai_approved_without_human_verification_is_high_risk`
  - Record: `ai_approved=True`, accepted FEN.
  - Expected: included with `ai_approved_without_human_verification`.

- [ ] `test_low_corpus_profile_forces_inclusion`
  - Provide corpus eval with profile seed count below threshold.
  - Expected: accepted record from that profile is included.

- [ ] `test_piece_confusion_risk_scores_queen_rook_and_pawn_rook`
  - Provide expected/current placements.
  - Expected: risk reasons include `piece_confusion_queen_rook` and `piece_confusion_pawn_piece`.

- [ ] `test_missing_crop_is_reported_but_export_succeeds`
  - Record references missing filename.
  - Expected: summary `missing_crop_count=1`, queue still written.

- [ ] `test_html_contains_crop_fen_risk_and_manual_fields`
  - Run export on one synthetic crop.
  - Expected: HTML includes record id, FEN, risk level, manual label field names, and image reference.

### Existing tests to update

- Add one test in `test_smoke_chess_quality.py` that documents the gap: smoke acceptance coverage alone does not imply accepted false-positive audit passed.
- Add one test near existing scan recovery tests in `test_chess_fen_recognition.py` if the exporter needs helper functions from `pymupdf_chess_extractor.py`.

## 9. Acceptance Criteria

- `scripts/export_chess_fen_accepted_audit.py` exists and is audit-only.
- The exporter includes all `requires_review` records.
- The exporter includes deterministic samples of accepted records, stable across runs.
- The exporter includes accepted records with high-risk warnings/methods/profiles independent of sample rate.
- `p010_d002` pawn-on-e5 versus rook-on-e5 class is always included as critical when present.
- AI-approved, arbiter-approved, or high-confidence records are never trusted blindly; they are audit candidates unless human/corpus evidence clears them.
- Output artifacts exist:
  - `accepted_audit_queue.json`
  - `accepted_audit_queue.jsonl`
  - `accepted_audit_review.html`
  - `accepted_audit_summary.json`
  - copied crops where available
  - grid overlays where possible
- HTML review cards include all required fields and do not mutate labels.
- Synthetic tests pass.
- Existing FEN recognition, smoke quality, and hardening tests still pass.

## 10. Files Likely To Modify During Implementation

Create:

- `scripts/export_chess_fen_accepted_audit.py`
- `test_chess_fen_accepted_audit.py`

Modify:

- `README.md` or `docs/chess-study-data-contracts.md` to document the audit command.
- `test_smoke_chess_quality.py` if smoke docs/tests should mention the new audit lane.
- `kindlemaster.py` only if this should become a first-class CLI subcommand later. V1 can remain a script.

Do not modify:

- runtime FEN publication logic in `pymupdf_chess_extractor.py`
- `chess_position_recognizer.py` recognition thresholds
- PGN export logic
- EPUB output

## 11. Suggested Implementation Tasks

### Task 1: Risk scoring helpers

**Files:**
- Create: `scripts/export_chess_fen_accepted_audit.py`
- Test: `test_chess_fen_accepted_audit.py`

- [ ] Define constants:

```python
HIGH_RISK_WARNINGS = {
    "side_to_move_inferred",
    "dense_board_area_crop_used",
    "reader_visible_crop_fen_used",
    "final_rendered_crop_fen_used",
    "sparse_exact_crop_consensus",
}

RECOVERY_METHOD_MARKERS = (
    "shift-recovered",
    "border-expanded",
    "border-refined",
    "bbox_recovered",
)
```

- [ ] Add `score_record_risk(record, profile_info, expected_fen=None) -> dict`.
- [ ] Add tests for warning risk, method risk, low-corpus risk, AI/arbiter risk, and `p010_d002`.

### Task 2: Deterministic sampling

**Files:**
- Modify: `scripts/export_chess_fen_accepted_audit.py`
- Test: `test_chess_fen_accepted_audit.py`

- [ ] Add `stable_sample_percent(record) -> float`.
- [ ] Add `select_audit_records(records, sample_rate, max_accepted_sample, profile_info)`.
- [ ] Assert repeated runs produce identical queue ordering.

### Task 3: Report reader and crop copier

**Files:**
- Modify: `scripts/export_chess_fen_accepted_audit.py`
- Test: `test_chess_fen_accepted_audit.py`

- [ ] Add smoke report reader for `quality_report.chess_fen.records` and `quality.chess_fen.records`.
- [ ] Reuse crop source resolution style from `scripts/export_chess_fen_review_queue.py`.
- [ ] Copy crops by filename where possible.
- [ ] Count missing crops without failing export.

### Task 4: Grid overlays and HTML review

**Files:**
- Modify: `scripts/export_chess_fen_accepted_audit.py`
- Test: `test_chess_fen_accepted_audit.py`

- [ ] Draw 8x8 grid overlay with Pillow when crop exists.
- [ ] Render `accepted_audit_review.html` with review cards.
- [ ] Ensure HTML includes static manual review fields but does not persist edits.

### Task 5: CLI and docs

**Files:**
- Modify: `scripts/export_chess_fen_accepted_audit.py`
- Modify: `README.md` or `docs/chess-study-data-contracts.md`

- [ ] Add CLI arguments:
  - positional `smoke_report`
  - `--output-dir`
  - `--crop-source-dir` repeatable
  - `--corpus-eval`
  - `--recognizer-eval`
  - `--sample-rate`
  - `--max-accepted-sample`
  - `--high-confidence-threshold`
  - `--low-grid-threshold`
- [ ] Document that the tool is audit-only and does not change accepted labels.

## 12. Validation Commands For Implementation Phase

Run after implementation, not for this planning-only change:

```powershell
python -m py_compile scripts\export_chess_fen_accepted_audit.py
python -m unittest test_chess_fen_accepted_audit.py test_chess_fen_pipeline_hardening.py test_chess_fen_recognition.py test_smoke_chess_quality.py
python kindlemaster.py test --suite quick
```

## 13. Rollback Strategy

- The tool is additive. Rollback is deleting `scripts/export_chess_fen_accepted_audit.py`, its tests, and docs.
- Do not couple v1 to runtime publication or smoke pass/fail status.
- If the queue is too noisy, tune sampling thresholds and risk score weights, not runtime FEN acceptance.
- Prefer over-including accepted records in audit over missing a visually wrong high-confidence FEN.

## 14. Risks And Mitigations

- Risk: audit queue becomes too large.
  - Mitigation: deterministic sampling plus always-include high-risk records.

- Risk: accepted FEN quality appears worse after audit.
  - Mitigation: report as honest audit coverage; do not reinterpret as runtime regression unless confirmed false positives exist.

- Risk: crop paths are missing from smoke reports.
  - Mitigation: support `--crop-source-dir` and still export records without images.

- Risk: profile metadata is missing.
  - Mitigation: mark `profile_eval_missing` and include accepted records from unknown profiles.

- Risk: AI approval is misread as authority.
  - Mitigation: risk scoring treats AI/arbiter approval without human verification as audit risk, not safety evidence.

- Risk: false confidence from syntactically valid FEN.
  - Mitigation: review cards emphasize crop image, grid overlay, square diff, risk reasons, and manual label fields.
