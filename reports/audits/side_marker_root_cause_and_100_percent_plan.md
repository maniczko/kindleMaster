# Deep audit: side-marker recognition, root causes and plan to 100% coverage

Date: 2026-07-10  
Repository: `maniczko/kindleMaster`  
Scope: detection of the diagram side-to-move marker (`△` = White, `▼` = Black), assignment of the marker to the correct diagram, propagation to `side_to_move`, and safe use in full FEN.

## Executive decision

The current implementation **cannot guarantee 100% automatic marker recognition**. The main limitation is no longer the absence of reports or a missing classifier. The code now has diagnostics, crop artifacts, a deterministic classifier and strict safety gates. The remaining blockers are architectural:

1. Marker-candidate generation is filtered by the final classifier before a reliable tight crop exists. This creates a detection/classification circular dependency.
2. Marker semantic trust is coupled to board-crop/FEN quality. A clear marker can be discarded because the board crop fails, even though the question “who moves?” is independent of piece-placement quality.
3. Markers are searched independently for every board in broad zones. There is no page-level one-to-one ownership assignment between marker candidates and diagrams.
4. The production thresholds are fixed-pixel, fixed-density rules, while real marker scale, scan contrast and noise vary by DPI and page.
5. The classifier’s reported 98.33% is measured on a synthetic-first corpus, not on a real Yusupov holdout.
6. Low-confidence board candidates are excluded from the canonical automatic path, so side-marker recognition never runs for them.
7. The “evidence tiers” implementation classifies already-existing evidence; it does not implement caption/PGN inference or evidence fusion.
8. The repository has no committed or securely attached real-job acceptance report proving performance on the user’s latest conversion.

For a fixed, fingerprinted Yusupov edition, **100% automatic `side_to_move` coverage is an achievable engineering target** after a one-time calibration/verification pass. It should be achieved by combining:

- trusted visual marker recognition,
- deterministic page-layout ownership,
- caption/notation/PGN inference where applicable,
- exact-hash reuse of human-verified labels for unresolved fixed-edition cases.

This does **not** mean that 100% of diagrams should be reported as `trusted_marker`. `trusted_marker` must remain reserved for visual evidence that passed the marker-specific gate. Full FEN must remain separately gated by board-placement quality.

---

## 1. Current pipeline reconstructed from code

The production study path is:

```text
PDF page
  -> detect_chess_diagrams()
  -> strict board candidates only
  -> copy board crops
  -> _attach_pdf_side_marker_evidence_to_study_diagrams()
       -> generic local marker probes
       -> optional clean-side-only local recovery
       -> _scan_chess_two_crop_review_artifacts()
            -> broad top/right/bottom/left zones
            -> component extraction
            -> marker classifier
            -> tight marker crop only for accepted candidate
       -> crop-quality gate
       -> trusted side promotion
  -> build_study_positions()
  -> FEN/PGN/reader/report pipeline
```

The safety path is intentionally conservative. A full FEN is not published unless board placement and side evidence pass their gates. This is correct. The problem is that the same gate is also used to decide whether marker semantics are trusted.

---

## 2. What works well

### 2.1. Crop artifacts and diagnostics are now explicit

The code records:

- board crop,
- marker search zones,
- selected zone,
- marker bbox,
- marker crop bbox,
- physical marker crop path,
- crop-quality reasons,
- classifier version/reason/confidence/symbol,
- manual-review reason,
- trusted-marker status.

This is sufficient to debug individual diagrams once a real output bundle is available.

### 2.2. Unsafe marker evidence is not silently promoted

The full-FEN gate rejects:

- multiple candidates,
- unclear symbols,
- cut-off markers,
- board-edge contamination,
- wrong marker candidates,
- missing marker evidence.

This protects precision and should be retained.

### 2.3. The code separates broad coverage reporting from full-FEN permission

The evidence dashboard distinguishes:

- `trusted_marker`,
- `human_verified`,
- `text_inferred`,
- `pgn_inferred`,
- `unknown`.

This is the correct vocabulary. The missing part is an inference/fusion engine that actually produces these non-marker sources reliably.

### 2.4. The synthetic classifier baseline is useful

`marker_shape_v2` demonstrates that the deterministic feature approach can distinguish clean outline and filled fixtures while rejecting synthetic negative classes. This is a useful unit-test baseline, but not a production accuracy claim.

---

## 3. Root causes that block 100%

## P0-1: Circular dependency between candidate generation and final trust

### Current behavior

`_scan_chess_best_marker_zone_candidate()` collects components from the marker zones, then keeps only candidates already classified as `trusted_marker`. If no candidate is already trusted, it returns `None`. The tight marker crop is therefore not generated from ambiguous-but-plausible candidates.

### Why this is structurally wrong

A broad search zone can contain:

- rank/file labels,
- caption text,
- scan noise,
- neighboring diagram elements,
- more than one connected component belonging to one printed marker.

The classifier performs best on a tight crop, but the current code asks the classifier to trust a candidate before the final tight-crop/adjudication stage is complete. Ambiguous zone-level evidence therefore prevents creation of the artifact needed to resolve the ambiguity.

### Required fix

Separate the process into independent stages:

```text
candidate generation (high recall)
-> candidate crop creation for top K
-> marker-shape classification
-> diagram ownership assignment
-> final marker semantic gate
```

Every plausible candidate should receive a crop and feature record. Trust must be decided only after crop-level classification and page-level assignment.

---

## P0-2: Marker semantic trust is incorrectly coupled to board-crop quality

### Current behavior

`_scan_chess_two_crop_trusted_side()` requires `board_crop_quality == pass` before returning `w` or `b`. The evidence resolver similarly requires both board and marker crop quality to pass before classifying a record as `trusted_marker`.

The quality gate can also clear an already trusted side when the board crop fails.

### Why this blocks the goal

There are two independent questions:

1. Does the marker unambiguously indicate White or Black?
2. Is the detected piece placement safe enough to publish a full FEN?

A board crop can be imperfect while a marker above it is perfectly readable. Clearing `side_to_move` because piece-placement evidence is weak reduces marker-recognition coverage for a reason unrelated to marker semantics.

### Required fix

Introduce independent fields:

```text
marker_semantic_status = trusted | review | missing
marker_semantic_side = w | b | unknown
marker_ownership_status = assigned | ambiguous | unassigned
board_placement_status = accepted | review
full_fen_allowed = marker semantic trusted AND ownership assigned AND board placement accepted
```

A trusted marker may populate `side_to_move`, while full FEN remains blocked if the board crop/placement is unsafe.

This is not loosening the FEN gate. It is correcting the separation of concerns.

---

## P0-3: Independent per-board search causes marker ownership conflicts

### Current behavior

Each board builds broad `top/right/bottom/left` zones and scans them independently. On pages with several diagrams, the same marker or text component can be visible to more than one board search.

The current logic then rejects multiple trusted candidates, but it does not solve the ownership problem globally.

### Required fix

Perform marker detection once per page:

1. detect all board boxes,
2. detect all marker candidates outside board interiors,
3. build a cost matrix between boards and markers,
4. solve one-to-one assignment,
5. use page/source layout priors,
6. leave only genuine unresolved ties for fallback evidence.

Recommended cost features:

- normalized edge distance to board,
- source-profile expected offset,
- zone compatibility,
- overlap with coordinates/text/other boards,
- marker-classifier confidence,
- uniqueness margin to next board,
- same-row/same-column layout relation.

A marker candidate must never be assigned to two diagrams.

---

## P0-4: Real diagram recall is not established before marker recognition

### Current behavior

The strict diagram path uses:

- `diagram_dpi=160` in the study configuration,
- `max_candidates_per_page=6`,
- grid-confidence threshold,
- sliding probe disabled in the detector default,
- low-confidence candidates disabled by default.

Only `manifest.diagrams` enter the normalized automatic path. `low_confidence_review_candidates` are kept separately and do not go through marker attachment, positions or final coverage.

The project’s own `masterkindle` threshold expects 540 diagrams, while the earlier real UI showed 274. This discrepancy is not yet reconciled by a verified expected-diagram manifest.

### Required fix

Create a canonical multi-pass detector:

```text
pass A: strict grid at 160/240 DPI
pass B: sliding/multi-scale probe
pass C: lower-confidence candidate recovery
-> global dedupe
-> stable diagram fingerprint
-> all candidates enter marker audit
```

Do not assign default `side_to_move = w` when side evidence is absent. Use `unknown`.

Low-confidence board records may remain in review for FEN, but marker recognition should still run on them if the board ownership bbox is usable.

---

## P0-5: No real Yusupov performance proof

### Current evidence

The committed marker corpus is synthetic-first. The classifier report gives:

- overall accuracy: 98.33%,
- outline accuracy: 96%,
- filled accuracy: 100%,
- synthetic negative false-trusted count: 0.

These results do not measure:

- real scan compression,
- anti-aliasing,
- printing noise,
- crop offset,
- rank/file glyph negatives,
- neighboring boards,
- page-specific marker scale,
- ownership accuracy.

The crop-QA manifest has 200 manual rows, but only 165 are labeled as clear outline/filled markers; 34 are bad crops and 1 is multiple. Therefore the existing benchmark cannot certify 100% trusted-marker coverage.

### Required fix

Build a secure real-data acceptance pack from the user’s conversion:

- minimal marker/search crops only,
- no full pages in the repository,
- labels for marker class, ownership, quality and expected side,
- train/calibration/holdout split by page/chapter,
- hard negatives from coordinates, letters, arrows, borders and captions,
- exact source/PDF fingerprint.

Production acceptance must be reported separately for:

- clear-marker subset,
- damaged/ambiguous subset,
- all diagrams.

---

## P1-1: Fixed thresholds are not scale- or scan-invariant

The detector and classifier use hard rules such as:

- grayscale thresholds 120/160,
- minimum component area 100,
- fixed 8/24 pixel bounds,
- fixed 24-pixel ownership margin,
- fixed density and aspect cutoffs,
- component-center constraints relative to crop.

These rules are sensitive to DPI, antialiasing, crop padding and marker print weight.

### Required fix

Use an ensemble of normalized features:

- Otsu/Sauvola adaptive thresholding,
- connected components at multiple thresholds,
- morphological close/open variants,
- contour approximation and triangularity,
- orientation/apex detection,
- interior fill ratio,
- border-to-interior ratio,
- features normalized by board-cell size,
- optional small calibrated image classifier on real crops.

The source convention should be modeled explicitly:

```text
outline upright triangle -> White
filled inverted triangle -> Black
```

Fill and orientation should agree, unless a source profile explicitly defines a different grammar.

---

## P1-2: Multiple components are treated too aggressively

The classifier returns an ambiguous result whenever more than one classified component exists alongside a trusted one. A harmless punctuation mark or broken triangle stroke can therefore invalidate a clear marker.

### Required fix

Use component grouping and dominance rather than raw component count:

- merge line segments belonging to one triangular contour,
- suppress tiny or text-like competitors,
- compare top score with runner-up,
- require a calibrated dominance margin,
- keep all candidate evidence in the report.

---

## P1-3: Evidence tiers are taxonomy, not an inference engine

`chess_side_to_move_evidence.py` determines the source type from fields and warning strings that already exist. It does not parse book captions or PGN to derive side-to-move.

### Required fix

Implement a deterministic evidence-fusion module:

```text
visual marker evidence
caption/OCR evidence: “White to move” / “Black to move”
notation evidence: `1.` versus `1...`
linked solution/PGN first mover
exact verified crop/source fingerprint
source-edition layout prior
```

Rules:

- conflicting trusted sources -> fail-safe review/build failure,
- marker + text/PGN agreement -> higher confidence,
- inferred evidence may satisfy coverage but not `trusted_marker`,
- full FEN remains controlled by board-placement and source-trust policy.

---

## P1-4: Diagram IDs are not robust enough for persistent learning

IDs such as `pNNN_dXX` depend on candidate order. If detector thresholds change, the candidate index may change, breaking benchmark matching and verified-label reuse.

### Required fix

Add a stable `diagram_fingerprint` from:

- source document SHA256,
- page number,
- normalized bbox coordinates,
- perceptual hash of the normalized board crop.

Verified labels and runtime comparisons should join by fingerprint first, legacy ID second.

---

## P1-5: The current report path does not close the feedback loop

The repository can generate detailed reports, but the latest local runtime evidence is not automatically exported into a safe, shareable bundle. Issue #254 addresses this operational gap.

This does not improve recognition directly, but without it real regressions cannot be inspected efficiently.

---

## 4. Why the previous work did not deliver 100%

The earlier issues improved contracts, reports and safety, but several were closed on synthetic or diagnostic evidence:

- The “zero marker-crop” work added propagation, counters and reason codes, but explicitly did not run the real Yusupov conversion.
- The corpus issue added synthetic crops rather than real holdout images.
- The classifier was tuned/evaluated on that synthetic corpus.
- The coverage dashboard reports evidence categories but does not create fallback evidence.

The system is therefore better instrumented, but it has not yet crossed the core production boundary: real-data candidate recall, correct board-marker ownership and calibrated classification.

---

## 5. Target architecture

Create a single canonical `SideMarkerPipeline` used by all conversion routes.

```text
Page image + canonical board boxes
  -> source-profile selection
  -> page-level adaptive marker candidate generator
  -> candidate crop bank (top K, no trust filtering)
  -> real-calibrated marker classifier
  -> board-marker bipartite assignment
  -> marker semantic gate
  -> evidence fusion
  -> side_to_move result
  -> separate full-FEN release gate
  -> audit bundle and acceptance metrics
```

### Proposed contracts

```json
{
  "diagram_fingerprint": "...",
  "marker_candidates": [
    {
      "candidate_id": "...",
      "bbox": [0, 0, 0, 0],
      "crop_path": "...",
      "features": {},
      "class": "outline|filled|negative|unclear",
      "side": "w|b|unknown",
      "confidence": 0.0
    }
  ],
  "marker_assignment": {
    "candidate_id": "...",
    "status": "assigned|ambiguous|unassigned",
    "ownership_confidence": 0.0,
    "runner_up_margin": 0.0
  },
  "marker_semantics": {
    "status": "trusted|review|missing",
    "side": "w|b|unknown",
    "confidence": 0.0
  },
  "side_to_move": {
    "value": "w|b|unknown",
    "source": "trusted_marker|human_verified|text_inferred|pgn_inferred|unknown",
    "confidence": 0.0
  },
  "board_placement_status": "accepted|review",
  "full_fen_allowed": false
}
```

---

## 6. Definition of 100%

The project needs explicit denominators.

### 6.1. For a fixed Yusupov edition

Required release criteria:

1. `expected_diagram_recall = 100%` against a verified manifest.
2. `marker_candidate_recall = 100%` on diagrams with a visible marker.
3. `marker_ownership_accuracy = 100%` on the holdout.
4. `clear_marker_classification_accuracy = 100%` on the fixed-edition holdout.
5. `false_trusted_marker_count = 0` on all hard negatives and ambiguous crops.
6. `side_to_move_coverage_rate = 100%` using allowed evidence sources.
7. `unknown_count = 0` for the fingerprinted edition.
8. `full_fen_safe_acceptance_rate` is reported separately and is not required to be 100%.

To reach item 6 without lying, unresolved fixed-edition cases may use exact-hash `human_verified` labels or deterministic caption/PGN evidence. They must not be renamed `trusted_marker`.

### 6.2. For arbitrary books/PDFs

No responsible system can guarantee 100% marker-only trust when the source may omit, damage or ambiguously place a marker. The correct universal target is:

- zero false trusted markers,
- maximum calibrated automatic coverage,
- explicit unresolved cases,
- automatic reuse of verified labels for identical source fingerprints.

---

## 7. Execution plan

## Phase 0 — obtain real truth and stop measuring synthetic success as production success

1. Finish #254 and export the latest real audit bundle.
2. Build the verified expected-diagram manifest for the exact PDF edition.
3. Label marker presence, side, ownership and crop quality for every diagram.
4. Establish a page/chapter-separated holdout.
5. Generate baseline metrics for the current main branch.

Exit criteria: every unknown is assigned to a concrete root-cause bucket.

## Phase 1 — fix candidate recall and ownership

1. Implement high-recall page-level candidate generation.
2. Generate top-K crops even when candidates are not yet trusted.
3. Add bipartite board-marker assignment.
4. Add source-profile priors for the Yusupov edition.
5. Run marker detection for low-confidence board candidates too.

Exit criteria:

- 100% candidate recall on visible-marker holdout,
- 100% ownership on holdout,
- zero candidate shared by two boards.

## Phase 2 — decouple marker trust from FEN trust

1. Add marker-semantic and ownership statuses.
2. Allow marker semantic trust independent of board crop.
3. Keep full FEN blocked unless board placement passes.
4. Update reader/report metrics accordingly.

Exit criteria: clear marker side remains available even when FEN stays review-only.

## Phase 3 — real-calibrated classification

1. Add adaptive threshold/contour features.
2. Add real hard negatives.
3. Train/calibrate optional lightweight classifier.
4. Tune confidence thresholds on calibration set only.
5. Freeze thresholds and evaluate holdout.

Exit criteria:

- 100% clear-marker accuracy for fixed-edition holdout,
- zero false trusted negatives,
- ambiguous examples remain non-trusted.

## Phase 4 — actual evidence fusion and fixed-edition automation

1. Implement caption and PGN-side inference.
2. Add stable fingerprints.
3. Add exact verified-label pack with source SHA guard.
4. Fuse evidence and detect conflicts.
5. Make unresolved/conflicting cases fail the edition acceptance gate.

Exit criteria: 100% `side_to_move` coverage for the fingerprinted edition with complete provenance.

## Phase 5 — regression and production gate

Every release must run:

- synthetic unit tests,
- real fixed-edition holdout,
- hard-negative suite,
- ownership tests on multi-board pages,
- DPI/contrast perturbation suite,
- full audit export.

A regression in false-trusted count or fixed-edition coverage blocks release.

---

## 8. Required GitHub tasks

1. **P0 — Decouple candidate extraction from trust and add page-level board-marker assignment.**
2. **P0 — Separate marker semantic trust from board/FEN release gates.**
3. **P0 — Build a real Yusupov acceptance corpus and end-to-end release gate.**
4. **P0 — Raise diagram recall with multi-pass detection and stable fingerprints.**
5. **P1 — Replace fixed thresholds with adaptive/real-calibrated marker classification.**
6. **P1 — Implement actual side-to-move evidence fusion and exact verified-label fallback.**
7. **P0 operational — Complete #254 audit bundle export.**

---

## Final recommendation

Do not spend another iteration adjusting one density threshold against synthetic fixtures. That will produce another local improvement without solving candidate recall, ownership or source variation.

The first implementation must change the architecture: generate candidate crops before trust, assign markers globally to boards, and separate marker semantics from full-FEN eligibility. In parallel, establish a real fixed-edition acceptance corpus. Those changes create a credible path to 100% automatic side-to-move coverage for the target book without weakening safety or mislabeling inferred evidence as visual trust.
