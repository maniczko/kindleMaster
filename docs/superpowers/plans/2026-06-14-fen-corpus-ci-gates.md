# FEN Corpus CI Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden chess FEN corpus/profile gates and GitHub READY enforcement so no FEN profile becomes corpus-accepted or runtime-publishable without verified labels, deterministic evaluation, zero false positives, and release-grade generalization proof.

**Architecture:** Keep the existing deterministic recognizer/profile/corpus pipeline, but split fast development checks from release/corpus proof. The strict release proof requires multiple real scanned profiles, holdout evaluation, accepted false-positive audit, and actionable failure reports; CI remains bounded by using committed fixtures and explicit contract gates rather than private PDFs.

**Tech Stack:** Python `unittest`, KindleMaster CLI, existing FEN scripts under `scripts/`, GitHub Actions READY workflow, JSON/Markdown reports.

---

## 1. Current State Findings

### `scripts/evaluate_chess_fen_recognizer.py`

Current defaults and behavior:

- `DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE = 0.835`.
- `DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN = 0.90`.
- A profile eval passes only when:
  - labels exist;
  - `exact_fen_accuracy >= min_exact_accuracy`;
  - `false_positive_count == 0`.
- It reports:
  - `exact_fen_accuracy`;
  - `false_positive_count`;
  - `false_positive_rate`;
  - `square_accuracy`;
  - `per_piece_accuracy`;
  - `per_piece_counts`;
  - `confusion`;
  - per-case `expected_fen`, `actual_fen`, `matched`, `false_positive`, confidence, warnings, and board diagnostics.
- Current gap: `square_accuracy`, per-piece confusion, and square-level diffs are reported evidence but not hard pass/fail gates.

### `scripts/evaluate_chess_fen_profile_holdout.py`

This script is present and already protects against training/evaluating on the same rows:

- validates labels before split;
- creates train/holdout split by `index % fold_count`;
- builds templates only from train rows;
- evaluates only holdout rows;
- fails on label validation failure, empty train split, empty holdout split, failed holdout eval, or any holdout false positive.

Current gap: profile readiness and corpus/release gates do not yet require this holdout report before profile acceptance.

### `scripts/evaluate_chess_fen_corpus.py`

Current defaults:

- `default_min_exact_accuracy = 0.90`.
- `default_min_seed_label_count = 20`.
- `min_profile_count = 1`.
- `min_confidence = DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE`, currently `0.835`.

Current behavior:

- Reads `reference_inputs/manifest.json`.
- Evaluates cases that declare `chess_fen_seed_labels`.
- Separates `chess_fen_font_board_candidate_labels` as review-only candidate profiles.
- Rejects label validation failures.
- Rejects verified seed label count below per-profile minimum.
- Fails if recognizer eval fails, including false positives.
- Fails if `evaluated_case_count < min_profile_count`.

Current gap: default `min_profile_count=1` is too permissive for release/corpus proof. The code can enforce `2` when passed explicitly, but the default CLI and release lane do not.

### `scripts/check_chess_fen_profile_ready.py`

Current defaults:

- `min_seed_labels = 20`.
- `min_confidence = 0.835`.
- `min_exact_accuracy = 0.90`.

Current behavior:

- Rejects missing source PDF.
- Rejects review-only label filenames.
- Calls `validate_chess_fen_labels()`.
- Requires at least `20` valid labels by default.
- Builds templates and runs recognizer eval.
- Sets `accepted_for_corpus=true` only when status is `ready`.

Current gap: does not require holdout evaluation or accepted/high-confidence false-positive audit before `accepted_for_corpus=true`.

### `scripts/build_chess_piece_templates.py`

Current behavior:

- Builds deterministic piece templates from label crops.
- Cleans stale generated template image files by default.
- Does not validate labels itself.

This is acceptable as a builder, but profile/corpus gates must ensure it only receives validated, human-verified labels and never holdout rows in holdout mode.

### `reference_inputs/manifest.json`

Current manifest state from inspection:

- `case_count = 13`.
- Strict scanned FEN seed profiles: `1`.
- Current seed profile:
  - `id = fundamenty_scan_chess_pdf`;
  - `chess_fen_seed_labels = reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl`;
  - `chess_fen_template_profile = fundamenty_merida_like`;
  - `chess_fen_seed_exact_accuracy_min = 0.9`;
  - no explicit `chess_fen_seed_min_count`, so corpus default `20` applies.
- Font-board candidate profiles in current manifest: `0`.

This means a strict release/corpus generalization gate with `min_profile_count=2` should currently fail until a second real scanned profile is committed.

### `kindlemaster.py`

Current CLI:

- `python kindlemaster.py corpus` has:
  - `--proof-profile standard|full|ci`;
  - `--fen-min-profile-count`, default `1`;
  - `--fen-min-seed-label-count`, default `20`.
- `python kindlemaster.py test --suite release` runs:
  - release unit tests;
  - corpus unit tests;
  - `python kindlemaster.py corpus --proof-profile <standard|ci>`.
- In GitHub READY release lane, `KINDLEMASTER_RELEASE_PROOF_PROFILE=ci`, so release uses `python kindlemaster.py corpus --proof-profile ci`.
- Current gap: release suite does not pass `--fen-min-profile-count 2`, so it inherits the CLI default `1`.

### `scripts/run_corpus_gate.py`

Current behavior:

- `run_corpus_gate(..., fen_min_profile_count=1, fen_min_seed_label_count=20)`.
- Always runs `evaluate_chess_fen_corpus()` and writes `fen_corpus_90.json`.
- Markdown surfaces:
  - evaluated profiles;
  - missing profiles;
  - min seed labels/profile;
  - valid seed labels;
  - exact accuracy;
  - false positives;
  - font-board candidate status;
  - reasons and next actions.

Current gap: default `fen_min_profile_count=1` makes the standard release gate permissive unless callers override it.

### `.github/workflows/ready-enforcement.yml`

Current READY workflow:

- `ready-quick` runs `python kindlemaster.py test --suite quick`.
- `ready-release` runs `python kindlemaster.py test --suite release` with `KINDLEMASTER_RELEASE_PROOF_PROFILE=ci`.
- It does not run an explicit separate FEN corpus gate command.
- Because release suite runs the corpus gate internally, FEN corpus is covered indirectly, but currently with `fen_min_profile_count=1`.

Current gap: READY does not make the stricter `2`-profile release proof explicit.

### `test_chess_fen_recognition.py`

Existing coverage already includes:

- recognizer exact FEN accuracy and false positive reports;
- holdout evaluator trains without holdout rows;
- holdout rejects review-only labels;
- corpus evaluator can require multiple profiles when called with `min_profile_count=2`;
- corpus evaluator rejects tiny seed profile by default;
- corpus evaluator rejects review-only labels before accuracy gate;
- profile readiness rejects review-only aid template;
- profile readiness rejects below-20 verified labels;
- profile readiness accepts synthetic 20-label profile.

Current gap: tests do not enforce that default release/corpus proof uses `2` profiles.

### `test_corpus_gate.py`

Existing coverage already includes:

- corpus gate merges smoke, premium, and FEN corpus reports;
- corpus gate fails when FEN corpus gate fails;
- `run_corpus_gate(..., fen_min_profile_count=2)` passes that value into `evaluate_chess_fen_corpus()`;
- `kindlemaster.py corpus --fen-min-profile-count 2` routes correctly.

Current gap: default `kindlemaster.py corpus` and release suite still route `fen_min_profile_count=1`.

### `docs/superpowers/plans/2026-05-27-fen-90-acceptance.md`

Existing plan/evidence already identifies the intended stronger generalization condition:

- at least two real scanned chess/FEN profiles;
- `evaluate_chess_fen_corpus.py --min-profile-count 2`;
- current one-profile gate is a weaker CI/dev proof, not broad generalization.

The new plan should codify that distinction in code paths and CI expectations.

## 2. Target Policy

### Development / Quick Lane

Purpose: fast bounded feedback without requiring private PDFs or expensive corpus runs.

- Keep quick lane bounded.
- Do not require `min_profile_count=2` in `ready-quick`.
- Run unit/contract tests that prove strict release gates exist.
- Allow current one-profile artifacts to remain usable for local development and regression debugging.

### Release / Corpus Proof Lane

Purpose: claim release-ready FEN generalization.

Required policy:

- `false_positive_count == 0`.
- `exact_fen_accuracy >= 0.90`.
- Per-profile `chess_fen_seed_exact_accuracy_min` may raise the threshold above `0.90`, but not lower it in release proof.
- `min_seed_label_count >= 20` per profile.
- `min_profile_count >= 2` for release/corpus proof.
- New profile must pass holdout evaluation before `accepted_for_corpus=true`.
- Accepted/high-confidence false-positive audit must run before profile readiness.
- Runtime-publishable profiles must come only from deterministic recognizer output and exact-crop verified labels, not AI/candidate profiles.

### CI Compatibility Policy

CI cannot depend on private/local PDFs. Therefore:

- CI must run with committed fixtures only.
- If only one real scanned profile exists in `reference_inputs/manifest.json`, a strict `min_profile_count=2` gate will correctly fail.
- Before enabling the `2`-profile READY hard fail, commit a second sanitized real scanned FEN profile with at least 20 verified labels, templates, and holdout/audit reports.
- Until the second committed profile exists, READY should include an explicit non-blocking or contract test proving the stricter command fails for the expected reason, while local release readiness remains blocked from claiming broad FEN generalization.

## 3. Proposed File Structure

Files to modify during implementation:

- `scripts/evaluate_chess_fen_corpus.py`  
  Add release-mode defaults/reporting, square-level false-positive details, and optional holdout/audit report requirements.

- `scripts/check_chess_fen_profile_ready.py`  
  Require holdout and accepted false-positive audit evidence before `accepted_for_corpus=true`.

- `scripts/run_corpus_gate.py`  
  Split dev/default profile-count behavior from release proof behavior and surface stricter FEN gate details in Markdown.

- `kindlemaster.py`  
  Route release/corpus proof to the stricter FEN profile count or expose a named release-ready switch.

- `.github/workflows/ready-enforcement.yml`  
  Add explicit FEN corpus gate step or make release command pass the strict FEN profile count after a second fixture exists.

- `test_chess_fen_recognition.py`  
  Add tests for holdout/audit requirements and richer failure summaries.

- `test_corpus_gate.py`  
  Add tests for release proof FEN min profile count defaults and actionable failure reports.

- `test_github_ready_enforcement.py`  
  Add tests that READY workflow includes the explicit FEN corpus gate contract.

- `test_kindlemaster_entrypoint.py`  
  Add tests that release/corpus command routing uses the desired FEN proof settings.

- `reference_inputs/manifest.json`  
  Eventually add a second committed real scanned chess FEN seed profile. Do not add a fake profile.

Files to create if not already implemented:

- `scripts/export_chess_fen_accepted_audit.py`  
  Accepted/high-confidence false-positive audit, planned separately but required by this gate.

## 4. Ordered Task Breakdown

### Task 1: Codify FEN Gate Modes

**Files:**

- Modify: `scripts/run_corpus_gate.py`
- Modify: `kindlemaster.py`
- Test: `test_corpus_gate.py`
- Test: `test_kindlemaster_entrypoint.py`

- [ ] Add a named policy distinction:
  - `dev`: `min_profile_count=1`;
  - `release`: `min_profile_count=2`;
  - `ci`: use committed fixture policy, with a transition path until second profile exists.
- [ ] Add a helper in `scripts/run_corpus_gate.py` such as `_fen_min_profile_count_for_proof_profile(proof_profile, explicit_value)`.
- [ ] Keep explicit `--fen-min-profile-count` as the highest-precedence override.
- [ ] Make `proof_profile="standard"` and `proof_profile="full"` resolve to `2` when no explicit override is passed.
- [ ] Decide whether `proof_profile="ci"` resolves to `1` during transition or `2` after the second committed profile is added.
- [ ] Add test: `run_corpus_gate(proof_profile="standard")` calls `evaluate_chess_fen_corpus(..., min_profile_count=2)`.
- [ ] Add test: explicit `fen_min_profile_count=1` still routes as `1` for bounded developer diagnostics.
- [ ] Add test: release suite invokes corpus gate with the release FEN proof policy.

Expected outcome:

- The code stops treating one profile as enough for standard/full release proof.
- Developers can still run bounded one-profile diagnostics intentionally.

### Task 2: Require Holdout Evidence For Profile Readiness

**Files:**

- Modify: `scripts/check_chess_fen_profile_ready.py`
- Modify: `scripts/evaluate_chess_fen_profile_holdout.py`
- Test: `test_chess_fen_recognition.py`

- [ ] Add optional arguments to profile readiness:
  - `holdout_eval_path`;
  - `require_holdout=True` for release/profile-ready mode;
  - `fold_count=5`;
  - `holdout_fold=0`.
- [ ] When `require_holdout=True`, run or load `evaluate_chess_fen_profile_holdout()`.
- [ ] Fail readiness if holdout status is not `passed`.
- [ ] Fail readiness if holdout `false_positive_count > 0`.
- [ ] Include holdout `exact_fen_accuracy`, `false_positive_count`, and compact failure cases in `next_required_actions`.
- [ ] Add test: profile readiness fails when regular eval passes but holdout fails.
- [ ] Add test: holdout rows are not used to build templates.
- [ ] Add test: holdout failure appears in `next_required_actions`.

Expected outcome:

- A new profile cannot become `accepted_for_corpus=true` by training and testing on the same rows.

### Task 3: Require Accepted False-Positive Audit Before Readiness

**Files:**

- Create or modify: `scripts/export_chess_fen_accepted_audit.py`
- Modify: `scripts/check_chess_fen_profile_ready.py`
- Test: `test_chess_fen_recognition.py`

- [ ] Define required audit artifact fields:
  - `status`;
  - `audited_count`;
  - `high_confidence_sample_count`;
  - `accepted_case_count`;
  - `false_positive_count`;
  - `risk_score_max`;
  - `known_bad_regression_status`;
  - `square_diffs`.
- [ ] Profile readiness should require `false_positive_count == 0`.
- [ ] Profile readiness should require `known_bad_regression_status == "passed"` when the audit includes known-bad fixtures.
- [ ] Profile readiness should fail if the audit artifact is missing in release mode.
- [ ] Add test: missing accepted audit fails profile readiness.
- [ ] Add test: audit false positive fails profile readiness even if recognizer eval passes.
- [ ] Add test: audit failure lists square-level diffs in next actions.

Expected outcome:

- High-confidence accepted-looking candidates are sampled and audited before profile/corpus promotion.

### Task 4: Enrich Corpus Failure Reporting

**Files:**

- Modify: `scripts/evaluate_chess_fen_corpus.py`
- Modify: `scripts/run_corpus_gate.py`
- Test: `test_chess_fen_recognition.py`
- Test: `test_corpus_gate.py`

- [ ] Include per-profile `failure_reasons` list, not only a single `failure_reason`.
- [ ] Include `false_positive_cases` with:
  - `id`;
  - `crop_path`;
  - `expected_fen`;
  - `actual_fen`;
  - `confidence`;
  - `warnings`;
  - square-level diffs when available.
- [ ] Include per-piece confusion summary in each failed profile summary.
- [ ] Include `holdout_status`, `holdout_false_positive_count`, and `holdout_failure_cases` when available.
- [ ] Extend Markdown to show:
  - FEN next actions;
  - per-profile failures;
  - per-piece confusion top items;
  - false-positive IDs.
- [ ] Add test: false positive with `exact_fen_accuracy >= 0.90` still fails and appears in Markdown.
- [ ] Add test: corpus gate returns actionable next steps for one-profile manifest with `min_profile_count=2`.

Expected outcome:

- A failed FEN corpus gate tells the next agent exactly what to fix.

### Task 5: READY Workflow Contract

**Files:**

- Modify: `.github/workflows/ready-enforcement.yml`
- Modify: `test_github_ready_enforcement.py`
- Modify: `docs/github-ready-enforcement.md`
- Modify: `docs/toolchain-matrix.md`

- [ ] Add an explicit FEN corpus gate step in `ready-release`, or update the release command to pass `--fen-min-profile-count 2` after a second committed profile exists.
- [ ] If transition mode is needed, add a separate contract step:

```powershell
python kindlemaster.py corpus --proof-profile ci --fen-min-profile-count 1
```

and a documented local release proof command:

```powershell
python kindlemaster.py corpus --proof-profile standard --fen-min-profile-count 2
```

- [ ] Ensure CI does not require private/local PDF paths.
- [ ] Upload `reports/corpus/fen_corpus_90.json` and any accepted-audit artifacts.
- [ ] Add test: READY workflow text includes the explicit FEN gate or release command with strict FEN proof.
- [ ] Add docs explaining that one-profile CI proof is not a full FEN generalization claim.

Expected outcome:

- READY enforcement makes the FEN corpus gate visible and auditable.
- Release claims cannot accidentally rely on the one-profile default.

### Task 6: Add Second Real Scanned Profile Before Hard-Failing `min_profile_count=2` In CI

**Files:**

- Modify: `reference_inputs/manifest.json`
- Add: `reference_inputs/chess_fen/labels/<profile>_seed_positions.jsonl`
- Add: `reference_inputs/chess_fen/templates/<profile>/`
- Add: `reports/chess_fen/evals/<profile>_holdout_latest.json` if generated artifacts are retained as evidence

- [ ] Select a repo-committable real scanned chess PDF fixture or crop manifest.
- [ ] Create at least 20 human-verified labels.
- [ ] Validate labels.
- [ ] Build templates.
- [ ] Run holdout evaluation.
- [ ] Run accepted false-positive audit.
- [ ] Run corpus gate with `--fen-min-profile-count 2`.
- [ ] Add manifest entry only after validation, holdout, audit, and corpus eval pass.

Expected outcome:

- GitHub CI can enforce `min_profile_count=2` without private assets.

## 5. Tests To Add Or Update

### Existing Test Coverage To Preserve

- Review-only labels fail before accuracy gate.
- Tiny seed profiles fail default `20` label minimum.
- Explicit `min_profile_count=2` fails one-profile manifest.
- False positives fail recognizer eval.
- Holdout evaluator does not train on holdout rows.

### New Tests

- [ ] `test_chess_fen_corpus_release_default_requires_two_profiles`
  - Build a synthetic manifest with one valid profile.
  - Run the release/default corpus gate path.
  - Expect `status="failed"` and `missing_profile_count=1`.

- [ ] `test_chess_fen_corpus_dev_override_can_use_one_profile`
  - Run with explicit `fen_min_profile_count=1`.
  - Expect profile count failure is not reported.

- [ ] `test_profile_ready_requires_holdout_when_release_mode`
  - Provide labels that pass direct eval but fail holdout.
  - Expect `accepted_for_corpus=false`.

- [ ] `test_profile_ready_requires_accepted_audit_when_release_mode`
  - Omit accepted audit artifact.
  - Expect a clear `accepted_audit_missing` issue.

- [ ] `test_false_positive_fails_even_when_exact_accuracy_passes`
  - Build 20 labels where 18/20 exact pass but one wrong high-confidence FEN is emitted while threshold is 0.90.
  - Expect status `failed` because `false_positive_count > 0`.

- [ ] `test_corpus_failure_report_includes_square_diffs`
  - Use expected pawn-vs-rook mismatch evidence.
  - Expect square diff in JSON/Markdown.

- [ ] `test_ready_workflow_exposes_fen_corpus_gate`
  - Assert `.github/workflows/ready-enforcement.yml` contains explicit FEN gate evidence or release command using strict FEN proof.

- [ ] `test_release_suite_routes_strict_fen_profile_count`
  - Patch `_run_bounded_command`.
  - Expect release path calls corpus gate with strict FEN profile policy.

## 6. Failure Reporting Requirements

Every failed FEN corpus/profile gate should report:

- `status`.
- `min_profile_count`.
- `evaluated_case_count`.
- `missing_profile_count`.
- `default_min_seed_label_count`.
- `failed_case_count`.
- `total_false_positive_count`.
- `overall_exact_fen_accuracy`.
- `next_required_actions`.
- For each profile:
  - profile ID;
  - labels path;
  - template profile;
  - valid label count;
  - min seed label count;
  - exact FEN accuracy;
  - square accuracy;
  - false positive count;
  - holdout status;
  - accepted audit status;
  - top per-piece confusion;
  - false positive cases with square diffs.

Example next action:

```json
[
  "add 1 real scanned chess FEN profile(s) to reach min_profile_count=2; each needs at least 20 manually verified labels",
  "run holdout evaluation for woodpecker_method_probe before profile readiness",
  "fix false positive p010_d002: e5 expected black rook, actual black pawn"
]
```

## 7. CI / READY Integration Policy

### Quick Lane

- Keep `ready-quick` bounded.
- Do not run full FEN corpus proof here.
- Keep `python kindlemaster.py test --suite quick`.

### Release Lane

Target command after second committed FEN profile exists:

```powershell
python kindlemaster.py test --suite release
```

Internally, release should run a corpus gate equivalent to:

```powershell
python kindlemaster.py corpus --proof-profile ci --fen-min-profile-count 2 --fen-min-seed-label-count 20
```

If CI must remain green before the second profile is committed, use a transition plan:

- keep CI release gate at `--fen-min-profile-count 1`;
- add a separate report-only strict command in docs/local release checklist;
- add tests proving `--fen-min-profile-count 2` fails the current manifest for the expected reason;
- switch CI hard gate to `2` in the same PR that adds the second profile.

### Local Release Candidate

Local release-candidate proof should always use:

```powershell
python kindlemaster.py corpus --proof-profile standard --fen-min-profile-count 2 --fen-min-seed-label-count 20
```

## 8. Acceptance Criteria

- `false_positive_count > 0` fails every profile/corpus/release FEN gate.
- `exact_fen_accuracy < 0.90` fails every profile/corpus/release FEN gate.
- A profile with fewer than 20 verified labels fails by default.
- Release/corpus proof requires at least 2 strict scanned FEN profiles.
- A one-profile manifest fails when `min_profile_count=2` and reports an actionable next step.
- Font-board candidate profiles never satisfy strict scanned FEN profile count.
- New profiles require holdout evaluation before `accepted_for_corpus=true`.
- Profile readiness requires accepted false-positive audit before `accepted_for_corpus=true`.
- READY workflow makes the FEN corpus gate visible and stores its artifacts.
- CI remains runnable without private/local PDF files.

## 9. Rollback Strategy

- Add stricter checks in report mode before making them release hard failures.
- Keep explicit `--fen-min-profile-count 1` available for bounded developer diagnostics.
- If READY starts failing only because the second public profile is not yet committed, revert the workflow hard-fail switch but keep tests and reporting.
- Do not relax `false_positive_count == 0`.
- Do not let font-board review candidates count as strict scanned profiles.
- Prefer failed release readiness over false runtime-publishable FEN.

## 10. Risks And Mitigations

- Risk: CI fails because only one real scanned profile is committed.  
  Mitigation: stage the change; add second public profile before switching READY hard gate to `2`.

- Risk: release becomes slower.  
  Mitigation: keep quick lane bounded, run strict proof only in release/corpus lanes, and reuse committed labels/templates.

- Risk: overfitting to two similar profiles.  
  Mitigation: require profile-level reporting and eventually add profile-family diversity metadata.

- Risk: false-positive audit creates review burden.  
  Mitigation: deterministic sampling plus high-risk targeting; do not audit every accepted case in quick lanes.

- Risk: reports become noisy.  
  Mitigation: summarize top blockers first and keep full false-positive/square-diff detail in JSON artifacts.

## 11. Validation Commands

After implementation, run:

```powershell
python -m py_compile scripts/evaluate_chess_fen_recognizer.py scripts/evaluate_chess_fen_corpus.py scripts/evaluate_chess_fen_profile_holdout.py scripts/check_chess_fen_profile_ready.py scripts/run_corpus_gate.py kindlemaster.py
python -m unittest test_chess_fen_recognition.py test_corpus_gate.py test_kindlemaster_entrypoint.py test_github_ready_enforcement.py
python kindlemaster.py corpus --proof-profile standard --fen-min-profile-count 2 --fen-min-seed-label-count 20
```

If the third command fails because the repo still has only one real scanned profile, the failure is expected until Task 6 lands. The report must show `missing_profile_count=1` and a next action that names the missing second profile requirement.

## 12. Self-Review Checklist

- This plan confirms current defaults.
- This plan separates quick/dev proof from release/corpus proof.
- This plan requires `false_positive_count == 0`.
- This plan requires exact FEN accuracy at least `0.90`.
- This plan preserves minimum `20` verified labels per profile.
- This plan raises release/corpus proof to at least `2` real scanned profiles.
- This plan requires holdout evaluation for new profiles.
- This plan requires accepted false-positive audit before profile readiness.
- This plan keeps CI independent of private/local PDFs.

