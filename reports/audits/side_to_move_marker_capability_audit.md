# Side-to-move marker capability audit

Date: 2026-07-07
Repo: `maniczko/kindleMaster`
Scope: technical audit, gap analysis, and plan toward high/100% marker-side coverage.

## 1. Executive summary

**Decision: current GitHub tasks are not enough to guarantee 100% `trusted_marker` coverage.**

They are now enough to build an auditable measurement loop and materially improve marker recognition, because #241-#246 have been implemented and merged through PR #247-#252. However, the repository still does not prove 100% recognition on the real Yusupov PDF. The remaining gap is not UI. The gap is real runtime evidence: real conversion artifacts, real marker crops, and a real Yusupov calibration run.

The important distinction is:

- `trusted_marker_rate`: side to move is trusted from a valid marker crop plus classifier and gates.
- `side_to_move_coverage_rate`: side to move is known from any allowed evidence tier, such as `trusted_marker`, `human_verified`, `text_inferred`, or `pgn_inferred`.

**100% trusted marker from visual markers only is not a safe target unless every diagram has an unambiguous visible marker and the runtime crop chain succeeds for every diagram.**

**100% side-to-move coverage is more realistic** if the system uses the evidence tiers added in #246, but only if inferred and human-verified sources remain clearly separated from `trusted_marker` and full-FEN gates stay conservative.

The minimal path to real improvement is:

1. Run a fresh real Yusupov conversion on current `main`.
2. Inspect `why_side_to_move_not_trusted.*` from #241.
3. Run the 200-row QA benchmark against that real job output using #242.
4. Check whether #243 actually fixes `Marker crop = 0` on the real PDF.
5. If marker crops exist, validate #245 classifier on those real marker crops or a local real-crop fixture pack.
6. Use #246 dashboard to separate trusted marker coverage from broader side-to-move coverage.

## 2. Current mechanism inventory

| Mechanism | File/code | Test/evidence | Report | Synthetic coverage | Real Yusupov evidence | Status | Gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Board crop / tight board bbox | `pymupdf_chess_extractor.py`, `chess_auto_flow.py` | PR #195, side-marker tests | crop metrics | yes | not proven in this audit | partial | real runtime rates needed |
| Marker search zones | `pymupdf_chess_extractor.py` | PR #196/#249 tests | `side_marker_assignment`, `why_side_to_move_not_trusted` | yes | not proven in this audit | partial | real Yusupov run needed |
| Marker bbox | `pymupdf_chess_extractor.py` | PR #196/#249 | `marker_bbox`, audit report | yes | not proven in this audit | partial | validate on real diagrams |
| Marker crop generation | `pymupdf_chess_extractor.py`, `chess_auto_flow.py` | PR #249 | `marker_crop_generation_rate`, `marker_crop_not_generated_count` | yes | not proven; PR #249 says real Yusupov run was not done | partial | run real job and inspect rate |
| Marker crop quality | `pymupdf_chess_extractor.py`, `chess_side_to_move_trust_audit.py` | PR #196/#249 | `marker_crop_quality_pass_rate` | yes | not proven in this audit | partial | real fail reason distribution needed |
| Marker classifier | `chess_marker_crop_classifier.py`, `pymupdf_chess_extractor.py` | PR #251, `test_chess_marker_classifier.py` | `marker_crop_classifier_report.json` | yes | no real crop validation yet | partial | synthetic-first corpus; local real pack needed |
| Trusted marker gate | `chess_fen_hardening.py`, `chess_fen_ml_acceptance.py`, `chess_side_to_move_evidence.py` | PR #197/#200/#252 | coverage dashboard | yes | not proven on real job | partial | verify false trusted rate = 0 on real job |
| Side-to-move propagation | `chess_auto_flow.py`, `chess_study_export.py`, `chess_side_to_move_evidence.py` | PR #247/#252 tests | `side_to_move_coverage_dashboard.*` | yes | not proven on real job | partial | real job coverage needed |
| Full FEN propagation | `chess_fen_hardening.py`, `chess_fen_ml_acceptance.py` | PR #200, hardening tests | full-FEN blockers | yes | not proven on real job | partial | must stay conservative |
| Benchmark 200 QA rows | `chess_crop_qa_benchmark.py`, `scripts/evaluate_chess_crop_qa_benchmark.py` | PR #198/#248 tests | `crop_qa_regression_diff.*` | yes | only after a real `--job-output` run | partial | run against latest real output |
| Real marker fixtures | `reference_inputs/chess_fen/marker_crops/**` | PR #250 | corpus manifest | synthetic-first | real local pack not committed | partial | real/anon local fixture pack required |
| Coverage metrics | `chess_side_to_move_evidence.py` | PR #252 tests | `side_to_move_coverage_dashboard.*` | yes | not proven on real job | partial | run on real Yusupov output |
| Reader UI | `frontend/src/App.tsx`, `app.py` | PR #229 | UI only | yes | screenshot earlier showed old UI before #229 | complete for visibility | not a marker-recognition mechanism |

## 3. Issue / PR audit

| Issue/PR | Goal | Delivered | Recognizes marker? | Blocks errors? | Status | Recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| #181 / #183 | Separate board crop and marker crop | Architecture for crop separation, marker zones, diagnostics | partially | yes | partial | Keep as base contract. |
| #184 / PR #195 | Tight `board_bbox` / `board_crop` | Tight board crop and validation | no, board only | yes | partial | Keep; verify real pass rate. |
| #185 / PR #196 | Final tight marker crop | Search-zone preview vs final crop, bbox/crop fields, quality | partially | yes | partial | Validate on real Yusupov. |
| #186 / PR #197 | Block bad `side_to_move` promotion | Conflict/ambiguous marker stays review-only | no | yes | complete | Keep gates strict. |
| #187 / PR #198 | 200-row crop QA benchmark | Evaluation-only benchmark, no runtime promotion | no | yes | partial | Now use #242 to compare real jobs. |
| #188 / PR #199 | Reason codes and diagnostics | Better metrics and review HTML | no | yes | complete | Use for debugging. |
| #189 / PR #200 | Crop-quality FEN gates | Full FEN blocked unless crop evidence passes | no | yes | complete | Do not loosen. |
| #228 / PR #229 | Reader visibility | UI shows Chess Reader despite partial data | no | no | complete | UI solved; not recognition. |
| #230 | Earlier audit | Identified real gaps | no | no | complete as planning | Superseded by this audit. |
| #241 / PR #247 | Per-diagram trust report | `why_side_to_move_not_trusted.json/md/html` | no | diagnostic | complete | Run on real output. |
| #242 / PR #248 | Benchmark vs runtime | `--job-output` comparison | no | diagnostic | complete | Run against real Yusupov output. |
| #243 / PR #249 | Fix zero marker-crop diagnostics | Propagates full marker-crop chain and rates | partially | yes | partial | Critical: real run still not done. |
| #244 / PR #250 | Marker crop corpus | Synthetic/minimal corpus, no full pages | indirectly | no | partial | Add real local fixture pack. |
| #245 / PR #251 | Marker classifier tuning | `marker_shape_v2`, corpus accuracy 0.9833, false trusted 0 | yes, on corpus | yes | partial | Validate on real crops. |
| #246 / PR #252 | Evidence tiers/dashboard | Separates trusted/human/text/PGN/unknown and writes dashboard | no classifier | yes | complete for reporting | Use to target coverage without lying. |

## 4. Evidence chain audit

| Chain step | Field/artifact | Produced in runtime? | Tested? | Real evidence? | Gap | Next action |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Diagram detection | `diagram_id`, `chess_diagrams.json` | yes | yes | unknown | need real job output | Run Yusupov job and inspect detection count. |
| Stable diagram ID | `diagram_id` | yes | partial | unknown | stability across reruns not measured here | Compare latest output to benchmark IDs. |
| Tight board bbox | `board_bbox`, `tight_board_bbox` | yes | yes | unknown | no current real rates | Use #241 report. |
| Board crop quality | `board_crop_quality`, fail reasons | yes | yes | unknown | need rate distribution | Use #241/#248. |
| Marker search zones | `marker_search_zones`, `side_marker_search_bbox` | yes after #249 | yes | unknown | real existence/rate unknown | Inspect `marker_search_zone_coverage_rate`. |
| Selected marker zone | `selected_marker_zone` | yes/partial | yes | unknown | may be absent or inferred from zones | Inspect per-diagram report. |
| Marker bbox | `marker_bbox`, `side_marker_bbox` | yes | yes | unknown | actual detection rate unknown | Inspect `marker_bbox_detection_rate`. |
| Marker crop bbox | `marker_crop_bbox` | yes | yes | unknown | physical write may fail | Inspect `marker_crop_not_generated_count`. |
| Side marker crop path | `side_marker_crop_path` | yes after #249 | yes | unknown | previous user run had 0 marker crop | Fresh real run required. |
| Marker crop quality | `marker_crop_quality` | yes | yes | unknown | fail reason distribution unknown | Inspect `marker_crop_quality_pass_rate`. |
| Symbol classification | `symbol`, `marker_classifier_reason`, confidence | yes after #251 | yes | synthetic corpus only | no real-crop proof | Run classifier on real marker crops. |
| Side to move detected | `side_to_move_detected`, `side_to_move` | yes | yes | unknown | real coverage unknown | Use dashboard #252. |
| Confidence | `marker_classifier_confidence`, `side_to_move_confidence` | yes | yes | unknown | calibration on real images missing | Calibrate thresholds with real pack. |
| Trusted marker | `side_marker_status=trusted_marker` | yes | yes | unknown | real trusted rate unknown | Measure on real job. |
| Full FEN propagation | `full_fen_allowed`, full-FEN status | yes | yes | unknown | should remain conservative | Verify full-FEN safe acceptance. |
| PGN/reader output | Chess Reader / PGN blocks | yes | yes | UI only | not marker recognition | Keep separate from recognition. |

## 5. Why not 100%

The earlier user-observed run had:

```text
Marker crop: 0
Trusted marker: 0
Side unknown: 274
```

On that run, one of these was true:

1. Marker search zones were not produced.
2. Marker bboxes were not found.
3. Marker crop bboxes existed but physical crop files were not written.
4. Crop files existed but were not propagated to the UI/metrics.
5. Quality gate classified all marker crops as missing/fail.
6. Classifier did not trust any crop.
7. The UI showed stale data from a pre-#247/#252 run.

After #247-#252, the repository can now measure those alternatives. It still does not prove that real Yusupov is fixed because the relevant PRs repeatedly note that full real runtime regeneration was not performed in the PR branch and that real/Yusupov-derived crops remain a local fixture-pack path, not committed evidence.

Specific answers:

- **Why `Marker crop = 0`?** Previously unknown. After #249, it should be measurable as `marker_crop_generation_rate` and `marker_crop_not_generated_count`. A fresh real run is required.
- **Why `Trusted marker = 0`?** Previously because no trusted marker evidence passed the chain. After #251, classifier exists; after #252, trusted vs inferred coverage is separated. A fresh real run is required.
- **Why `Side unknown = 274`?** Because no trusted or inferred evidence tier was available or propagated in that job. #252 can now distinguish `trusted_marker`, `human_verified`, `text_inferred`, `pgn_inferred`, and `unknown`.
- **Do marker search zones exist?** Code now produces/propagates them, but real rates need runtime output.
- **Does `marker_bbox` exist?** Code now records it, but real detection rate is unknown.
- **Does `side_marker_crop_path` exist?** Code now propagates it, but real generation rate is unknown.
- **Does classifier recognize symbols?** It recognizes synthetic fixture corpus with 0.9833 overall accuracy and 0 negative false trusted count, but real crop validation is missing.
- **Are gates too strict?** Gates are intentionally strict and should not be loosened. Missing or poor evidence should remain review-only.
- **Is benchmark evaluation-only?** Yes. It compares runtime output but does not promote labels into runtime truth.

## 6. Blocker classification

| Blocker class | How to detect | Fields/report | Count available now? | Next step |
| --- | --- | --- | ---: | --- |
| A. source artifact missing | no source image/crop path | `source_artifact_missing`, `source_html` | no real count | Run #241 report on real job. |
| B. diagram detection / unstable id | missing expected IDs vs benchmark | `missing_actual_count` in crop QA diff | no real count | Run #242 with `--job-output`. |
| C. board bbox/crop issue | board quality missing/fail | `board_crop_quality`, reasons | no real count | Inspect #241 output. |
| D. marker search zone missing/wrong | zero zones or wrong selected zone | `marker_search_zone_count`, `selected_marker_zone` | no real count | Inspect #241 output. |
| E. marker bbox not found | zones exist but bbox absent | `marker_bbox_exists=false` | no real count | Tune search-zone/bbox detection. |
| F. marker crop not generated | bbox exists but crop absent | `marker_crop_not_generated_count` | no real count | Fix physical crop writing/path propagation. |
| G. marker crop quality fail | crop exists, `marker_crop_quality=fail` | fail reasons | no real count | Improve crop padding/validation. |
| H. classifier missing | crop exists, no symbol/trust | `marker_classifier_reason` | no real count | Use #245 classifier on real crops. |
| I. classifier ambiguous/conflict | multiple/unclear/conflict | classifier reason/status | no real count | Keep review-only; improve edge-case handling. |
| J. gate blocks missing evidence | full FEN blocked | `full_fen_blocker`, blockers | no real count | Keep gate; supply evidence. |
| K. benchmark not connected to runtime | no `--job-output` run | crop QA diff missing/stale | no real count | Run #242 output. |
| L. no real fixture coverage | synthetic-only corpus | corpus source/policy | yes qualitatively | Add local real pack. |
| M. policy blocks runtime promotion | human/manual labels not allowed | `allowed_for_runtime_truth=false`, evidence tier policy | yes qualitatively | Define explicit human_verified policy only if desired. |

## 7. Metrics to reach target

| Metric | Definition | Numerator | Denominator | Source | Minimal target | Desired target | 100% realistic? |
| --- | --- | ---: | ---: | --- | ---: | ---: | --- |
| `diagram_detection_rate` | detected diagrams vs expected | detected diagrams | expected diagrams | `chess_diagrams.json`, benchmark | 0.95 | 0.99+ | possible, not guaranteed |
| `stable_diagram_id_rate` | same IDs across runs/benchmark | matched IDs | expected IDs | crop QA diff | 0.90 | 0.98 | possible with stable layout |
| `board_crop_quality_pass_rate` | valid board crops | pass board crops | diagrams | #241 | 0.95 | 0.99 | maybe |
| `marker_search_zone_coverage_rate` | zones produced | diagrams with zones | diagrams | #241 | 0.98 | 1.0 | likely possible |
| `marker_bbox_detection_rate` | marker bboxes found | bboxes found | diagrams with visible marker | #241 | 0.85 | 0.95+ | not if marker absent/ambiguous |
| `marker_crop_generation_rate` | physical marker crops generated | crops generated | diagrams with bbox | #241/#249 | 0.95 | 1.0 for bbox cases | yes for bbox cases |
| `marker_crop_quality_pass_rate` | marker crops pass validation | crop pass | crops generated | #241 | 0.85 | 0.95 | not for bad/multiple/unclear |
| `marker_classification_accuracy` | correct symbol classification | correct | labeled corpus | #251 report | 0.90 | 0.97+ | possible on bounded corpus |
| `trusted_marker_rate` | trusted from visual marker | trusted marker | diagrams | #252 dashboard | corpus-dependent | high if markers clear | 100% usually not safe |
| `trusted_side_to_move_rate` | trusted evidence tier | trusted tier | diagrams | #252 dashboard | corpus-dependent | high | 100% unlikely |
| `side_to_move_coverage_rate` | any valid source | trusted + verified + inferred | diagrams | #252 dashboard | 0.90 | 1.0 | possible with fallback + review |
| `manual_review_required_rate` | review-needed records | manual review | diagrams | #252 dashboard | decreasing trend | minimal | 0% unlikely |
| `system_suggestion_mismatch_rate` | wrong-side suggestions | mismatches | audited labeled rows | crop QA diff | 0 | 0 | yes, by review blocking |
| `full_fen_safe_acceptance_rate` | full FEN safely allowed | full-FEN allowed | diagrams | #252 + FEN reports | evidence-dependent | improve safely | 100% only if all evidence exists |

## 8. Gap-to-100% plan

### P0 - before further classifier work

1. Run fresh real Yusupov conversion on current `main`.
2. Inspect `reports/chess_fen/why_side_to_move_not_trusted.*`.
3. Run crop QA benchmark with `--job-output` against that real output.
4. Inspect `marker_crop_generation_rate`, `marker_crop_not_generated_count`, `marker_bbox_detection_rate`, and top blockers.
5. Decide whether failures are in search zones, bbox, crop writing, crop quality, classifier, or gates.

Expected after P0: exact per-diagram failure taxonomy, no guessing.

### P1 - improve marker recognition

1. Add/point to a local real marker-crop fixture pack.
2. Run `python kindlemaster.py ml evaluate-marker-crops` on both synthetic and real marker crops.
3. Tune `marker_shape_v2` thresholds if real false negative rates are high.
4. Preserve false trusted count at 0 for bad/multiple/unclear.

Expected after P1: high trusted marker rate on clear real marker crops, still review-only for unsafe crops.

### P2 - reach full coverage without lying

1. Use #246 evidence tiers.
2. Enable `human_verified` only under explicit policy.
3. Add `text_inferred` / `pgn_inferred` as coverage-only, not trusted marker.
4. Add batch review/export pipeline.

Expected after P2: possible 100% `side_to_move_coverage_rate`, but not necessarily 100% `trusted_marker_rate`.

## 9. Decision: are current GitHub tasks enough?

**Current GitHub tasks are enough to create an auditable pipeline and a safe path to high coverage, but not enough to guarantee 100% visual marker recognition.**

They are enough if the target is:

- produce diagnostics,
- compare benchmark to runtime,
- generate/propagate marker crop fields,
- classify marker crops on the synthetic fixture corpus,
- keep inferred/human/trusted sources separated.

They are not enough if the target is:

- prove 100% trusted marker rate on the real Yusupov PDF,
- prove classifier accuracy on real Yusupov marker crops,
- accept full FEN for every diagram without manual or inferred fallback.

## 10. Proposed missing work

### Issue A: Run real Yusupov calibration and publish side-to-move evidence report

Priority: P0

Goal: Run current `main` on the real Yusupov PDF and attach/output the actual rates from #241/#242/#252.

Acceptance criteria:

- `why_side_to_move_not_trusted.json/md/html` generated from real job.
- `crop_qa_regression_diff.json/md` generated with `--job-output`.
- `side_to_move_coverage_dashboard.json/md/html` generated.
- Report includes top blockers and real counts for marker zone, bbox, crop, quality, classifier, trusted marker, and coverage.

Tests:

- Existing quick and quality-critical suites.
- Manual verification of output files for real job.

Out of scope: changing classifier/gates.

### Issue B: Add local real marker-crop fixture pack support and calibration command

Priority: P0/P1

Goal: Support a non-committed local fixture pack of real/anonymized marker crops and evaluate classifier separately on it.

Acceptance criteria:

- CLI accepts `--corpus-root <path>` for real local marker crops.
- Report distinguishes `synthetic_committed` vs `local_real_fixture`.
- No real book pages are committed.
- False trusted count remains 0.

Out of scope: committing copyrighted full pages.

### Issue C: Tune marker search-zone geometry using real failure buckets

Priority: P1

Goal: Improve marker bbox/crop generation if real `marker_bbox_detection_rate` or `marker_crop_generation_rate` is low.

Acceptance criteria:

- Uses #241 top blockers to target failures.
- Improves real marker bbox/crop rates without increasing false trusted cases.

### Issue D: Human verified side-to-move policy

Priority: P1/P2

Goal: Define if and when `human_verified` can contribute to full FEN acceptance.

Acceptance criteria:

- Explicit config/policy gate.
- Audit trail with reviewer/source/date.
- No silent manual label promotion.

## 11. Final recommendation

Start here: run a fresh real Yusupov conversion on current `main`, then inspect #241/#242/#252 outputs.

Do not do this: do not loosen FEN gates or promote `inferred_only` to `trusted_marker` to make percentages look better.

Expected result after P0: exact numeric explanation of where side-to-move fails per diagram.

Expected result after P1: improved trusted marker rate on clear real crops and reliable classifier metrics on real/anonymized marker crop pack.

Expected result after P2: high or 100% side-to-move coverage using evidence tiers, without pretending all sources are trusted markers.

Is 100% trusted_marker realistic? **Not as a guaranteed target.** It depends on whether every diagram has a clear, unambiguous visible marker and whether crop/classifier/gates pass for all of them.

Is 100% side_to_move coverage realistic? **Yes, as a product target, if coverage can include `trusted_marker`, `human_verified`, `text_inferred`, and `pgn_inferred`, while full-FEN acceptance remains evidence-gated.**
