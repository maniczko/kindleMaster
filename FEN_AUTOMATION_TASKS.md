# FEN Automation Tasks

## Execution rules
- No full-FEN acceptance weakening.
- No AI direct machine acceptance.
- No heavy dependency introduction.
- Placement acceptance is separate from full FEN acceptance.
- Full FEN export and PGN replay must not silently rely on placement-only acceptance.

## Baseline
- Git status before changes: clean except `FEN_AUTOMATION_TASKS.md` after P0-00 creation; branch `codex/fen-placement-automation-p0` from `origin/main`.
- Python version: Python 3.14.3.
- Tests run before changes:
  - `python -m unittest test_chess_fen_pipeline_hardening.py test_chess_fen_ml_acceptance.py test_chess_auto_flow.py` -> 39 tests OK.
  - `python -m unittest test_chess_fen_recognition.py` -> timed out after 304 seconds; treated as baseline timeout, not implementation failure.
  - `python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/fundamenty_merida_like --output reports/chess_fen/evals/baseline_before_placement_acceptance.json` -> passed, 40/40 exact FEN, 0 false positives.
- Failures before changes: no targeted failures; long recognition test timed out.
- Current inferred FEN pipeline: `ChessFenResult.to_dict()` suppresses runtime `fen` when `side_to_move_inferred` is present without trusted marker/caption/exact-label evidence, while full placement can still be internally present.
- Current top blockers: seed recognizer benchmark shows correct placements but `side_to_move_inferred` warnings across cases; current full-FEN machine gate requires complete six-field FEN plus deterministic proof.

## Task board

| ID | Priority | Status | Task | Files | Tests | Result |
|---|---|---|---|---|---|---|
| P0-00 | P0 | DONE | Create execution task file | FEN_AUTOMATION_TASKS.md | Not required | Done |
| P0-01 | P0 | DONE | Baseline repo and test snapshot | FEN_AUTOMATION_TASKS.md | Existing targeted FEN tests | Done; recognition suite timed out |
| P0-02 | P0 | DONE | Add placement-level validation helpers | chess_fen_hardening.py; test_chess_fen_pipeline_hardening.py | python -m unittest test_chess_fen_pipeline_hardening.py | Done |
| P0-03 | P0 | DONE | Add placement-level machine acceptance | chess_fen_hardening.py; test_chess_fen_pipeline_hardening.py; test_chess_fen_ml_acceptance.py | python -m unittest test_chess_fen_pipeline_hardening.py; python -m unittest test_chess_fen_ml_acceptance.py | Done |
| P0-04 | P0 | DONE | Add placement acceptance fields to canonical FEN candidate rows | chess_auto_flow.py; test_chess_auto_flow.py | python -m unittest test_chess_auto_flow.py; python -m unittest test_chess_fen_pipeline_hardening.py | Done |
| P0-05 | P0 | DONE | Add placement-level selection to _select_fen_status | chess_auto_flow.py; test_chess_auto_flow.py | python -m unittest test_chess_auto_flow.py; python -m unittest test_chess_fen_pipeline_hardening.py | Done |
| P0-06 | P0 | DONE | Add placement metrics to auto summary | chess_auto_flow.py; test_chess_auto_flow.py | python -m unittest test_chess_auto_flow.py | Done |
| P0-07 | P0 | DONE | Preserve placement when side-to-move is inferred | chess_position_recognizer.py; test_chess_fen_recognition.py | targeted ChessFenResult tests; python -m unittest test_chess_fen_pipeline_hardening.py | Done |
| P0-08 | P0 | DONE | Categorize acceptance blockers | chess_auto_flow.py; test_chess_auto_flow.py | python -m unittest test_chess_auto_flow.py | Done |
| P0-09 | P0 | DONE | Add board detection quality artifact | scripts/chess_diagram_detection.py; test_chess_diagram_detection.py | python -m unittest test_chess_diagram_detection.py | Done |
| P0-10 | P0 | DONE | Add automation readiness evaluator | scripts/evaluate_fen_automation_readiness.py; test_fen_automation_readiness.py | python -m unittest test_fen_automation_readiness.py; python scripts/evaluate_fen_automation_readiness.py --help | Done |
| P0-11 | P0 | DONE | Add/adjust regression tests for placement-level automation | test_chess_fen_pipeline_hardening.py; test_chess_fen_ml_acceptance.py; test_chess_auto_flow.py; test_chess_fen_recognition.py | Targeted FEN tests | Done |
| P0-12 | P0 | DONE | Final code quality pass | All changed files | git diff; targeted tests; py_compile | Done |
| P1-01 | P1 | DONE | Expand verified template profile dataset | scripts/evaluate_chess_fen_label_inventory.py; test_chess_fen_dataset_tools.py | python -m unittest test_chess_fen_dataset_tools.py; inventory CLI | Done; inventory shows 0 valid human-verified labels under hardened contract |
| P1-02 | P1 | DONE | Build false-positive and cropped-board dataset | scripts/validate_chess_fen_audit_dataset.py; reference_inputs/chess_fen/audit_dataset; test_chess_fen_dataset_tools.py | python -m unittest test_chess_fen_dataset_tools.py; validator --help | Done |
| P1-03 | P1 | DONE | Persist square-level debug artifacts | scripts/export_chess_fen_square_debug_artifacts.py; scripts/evaluate_chess_fen_recognizer.py; test_chess_fen_square_debug_artifacts.py | python -m unittest test_chess_fen_square_debug_artifacts.py; exporter --help | Done |
| P1-04 | P1 | DONE | Add HTML placement review dashboard | scripts/build_chess_fen_placement_review_dashboard.py; test_chess_fen_placement_review_dashboard.py | python -m unittest test_chess_fen_placement_review_dashboard.py; dashboard --help | Done |
| P2-01 | P2 | DONE | Evaluate whether template matcher should be replaced or augmented | scripts/evaluate_chess_fen_template_strategy.py; test_chess_fen_template_strategy.py | python -m unittest test_chess_fen_template_strategy.py; strategy CLI; py_compile | Done; keep template matcher and collect evidence |

## Implementation log

### P0-00
- What changed: Created this execution task file before implementation.
- Why: The prompt requires this file as the source of truth for execution.
- Files changed: FEN_AUTOMATION_TASKS.md.
- Tests run: Not required.
- Result: Done.
- Remaining risk: None for task tracking.

### P0-01
- What changed: Captured baseline branch, Python version, targeted test result, long-suite timeout, and recognizer benchmark.
- Why: Establishes current behavior before adding placement-level automation.
- Files changed: FEN_AUTOMATION_TASKS.md.
- Tests run:
  - `python -m unittest test_chess_fen_pipeline_hardening.py test_chess_fen_ml_acceptance.py test_chess_auto_flow.py` -> OK.
  - `python -m unittest test_chess_fen_recognition.py` -> timed out after 304 seconds.
  - `python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/fundamenty_merida_like --output reports/chess_fen/evals/baseline_before_placement_acceptance.json` -> passed.
- Result: Baseline documented.
- Remaining risk: Full `test_chess_fen_recognition.py` is slow in this environment and needs targeted subsets or longer timeout for final validation.

### P0-02
- What changed: Added placement extraction, placement-only validation, placement normalization, and default full-FEN construction helpers.
- Why: Board placement is the visual layer that can be validated without side-to-move or other full-FEN metadata.
- Files changed: chess_fen_hardening.py; test_chess_fen_pipeline_hardening.py.
- Tests run: `python -m unittest test_chess_fen_pipeline_hardening.py` -> OK.
- Result: Done.
- Remaining risk: Placement validation deliberately does not call python-chess and therefore proves structure/plausibility, not legal chess state.

### P0-03
- What changed: Added `machine_accept_placement()` with deterministic source policy, confidence gate, placement warning blockers, and deterministic ensemble evidence checks.
- Why: Allows runtime to report reliable visual placement acceptance without weakening full-FEN acceptance.
- Files changed: chess_fen_hardening.py; test_chess_fen_pipeline_hardening.py.
- Tests run:
  - `python -m unittest test_chess_fen_pipeline_hardening.py` -> OK.
  - `python -m unittest test_chess_fen_ml_acceptance.py` -> OK.
- Result: Done.
- Remaining risk: Direct deterministic/template candidates are accepted at placement level without square alternatives, by design; full FEN remains strict.

### P0-04
- What changed: Extended canonical FEN candidate rows with placement value, normalized placement, placement validation flags, placement runtime status, blockers, trace, and policy.
- Why: Consumers can now see whether visual placement is accepted even when full FEN is not.
- Files changed: chess_auto_flow.py; test_chess_auto_flow.py.
- Tests run: `python -m unittest test_chess_auto_flow.py` -> OK.
- Result: Done.
- Remaining risk: Existing downstream consumers that ignore new fields remain compatible; new consumers must not treat placement as full FEN.

### P0-05
- What changed: Added `FEN_PLACEMENT_MACHINE_ACCEPTED` selection when placement passes and full FEN does not; `selected_value` remains `None`, `selected_placement` is populated.
- Why: Makes placement success explicit without weakening strict full-FEN export.
- Files changed: chess_auto_flow.py; test_chess_auto_flow.py.
- Tests run: `python -m unittest test_chess_auto_flow.py` -> OK.
- Result: Done.
- Remaining risk: Placement-only status is deliberately not part of `FEN_ACCEPTED_STATUSES`.

### P0-06
- What changed: Added placement metrics to status and auto summary: placement accepted count/rate, full-FEN accepted count/rate, placement review count.
- Why: Reports can now separate first visual automation target from full metadata acceptance.
- Files changed: chess_auto_flow.py; test_chess_auto_flow.py.
- Tests run: `python -m unittest test_chess_auto_flow.py` -> OK.
- Result: Done.
- Remaining risk: Historical dashboards may need UI work to visualize the new fields.

### P0-07
- What changed: `ChessFenResult.to_dict()` now emits `fen_suppressed_reason` and preserves `placement`, `placement_fen`, and `full_fen` when inferred side suppresses runtime `fen`.
- Why: Full FEN stays review-safe while placement remains visible for diagnostics and placement-level acceptance.
- Files changed: chess_position_recognizer.py; test_chess_fen_recognition.py.
- Tests run: targeted `ChessFenRecognitionTests` for inferred/trusted side behavior -> OK.
- Result: Done.
- Remaining risk: Full `test_chess_fen_recognition.py` timed out in baseline; targeted tests cover this specific behavior.

### P0-08
- What changed: Added `_classify_acceptance_blocker()` and category counts in acceptance blocker JSON/HTML.
- Why: Operators can distinguish crop/grid, recognition, placement, full-FEN, source-policy, confidence, metadata, PGN, and dependency blockers.
- Files changed: chess_auto_flow.py; test_chess_auto_flow.py.
- Tests run: `python -m unittest test_chess_auto_flow.py` -> OK.
- Result: Done.
- Remaining risk: Unknown blockers still map to `unknown` until explicitly classified.

### P0-09
- What changed: Added board detection quality records and JSON/JSONL artifacts under `reports/chess_fen/board_detection_quality.*`.
- Why: Crop/grid quality can now be audited separately from FEN recognition.
- Files changed: scripts/chess_diagram_detection.py; test_chess_diagram_detection.py.
- Tests run: `python -m unittest test_chess_diagram_detection.py` -> OK.
- Result: Done.
- Remaining risk: Artifact is diagnostic; it does not verify crop correctness without human/ground-truth labels.

### P0-10
- What changed: Added `scripts/evaluate_fen_automation_readiness.py` with callable function and CLI.
- Why: Gives a lightweight readiness report over produced auto-chess artifacts.
- Files changed: scripts/evaluate_fen_automation_readiness.py; test_fen_automation_readiness.py.
- Tests run:
  - `python -m unittest test_fen_automation_readiness.py` -> OK.
  - `python scripts/evaluate_fen_automation_readiness.py --help` -> OK.
- Result: Done.
- Remaining risk: Recommendation is heuristic based on current blocker categories.

### P0-11
- What changed: Added regression coverage across placement validation, machine placement acceptance, canonical FEN placement status, side-to-move suppression, blocker categories, board detection artifact, and readiness evaluator.
- Why: Protects placement-level automation from becoming accidental full-FEN acceptance.
- Files changed: test_chess_fen_pipeline_hardening.py; test_chess_auto_flow.py; test_chess_fen_recognition.py; test_chess_diagram_detection.py; test_fen_automation_readiness.py.
- Tests run: `python -m unittest test_chess_fen_pipeline_hardening.py test_chess_fen_ml_acceptance.py test_chess_auto_flow.py test_chess_diagram_detection.py test_fen_automation_readiness.py test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_result_preserves_placement_when_inferred_side_suppresses_runtime_fen test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_result_keeps_runtime_fen_with_trusted_side_evidence` -> 59 tests OK.
- Result: Done.
- Remaining risk: Full recognition suite remains slow and timed out during baseline.

### P0-12
- What changed: Reviewed diff/stat, verified no generated report artifacts are staged in git status, and py-compiled changed modules/tests.
- Why: Final quality pass for accidental syntax/debug/report noise.
- Files changed: FEN_AUTOMATION_TASKS.md.
- Tests run:
  - `python -m py_compile chess_fen_hardening.py chess_auto_flow.py chess_position_recognizer.py scripts/chess_diagram_detection.py scripts/evaluate_fen_automation_readiness.py test_chess_fen_pipeline_hardening.py test_chess_auto_flow.py test_chess_diagram_detection.py test_fen_automation_readiness.py` -> OK.
  - `git diff --stat` reviewed.
- Result: Done.
- Remaining risk: No localhost restart was done because no server/runtime web process was claimed live.

### P1-01
- What changed: Added `scripts/evaluate_chess_fen_label_inventory.py` to report label counts, valid human-verified counts, target gaps, and next actions per label file/profile.
- Why: Expanding verified template profiles must be driven by measured gaps, not invented labels.
- Files changed: scripts/evaluate_chess_fen_label_inventory.py; test_chess_fen_dataset_tools.py; FEN_AUTOMATION_TASKS.md.
- Tests run:
  - `python -m unittest test_chess_fen_dataset_tools.py` -> OK.
  - `python scripts/evaluate_chess_fen_label_inventory.py --help` -> OK.
  - `python scripts/evaluate_chess_fen_label_inventory.py reference_inputs/chess_fen/labels --target-per-profile 100 --output reports/chess_fen/label_inventory_p1.json` -> OK.
- Result: Done. Current inventory found 184 total label records but 0 valid human-verified labels under the hardened contract; all three label files are missing the target.
- Remaining risk: Historical labels may be visually correct but need migration/manual evidence fields before release/corpus readiness can count them.

### P1-02
- What changed: Added `scripts/validate_chess_fen_audit_dataset.py`, audit dataset README, and placeholder directory for future false-positive/cropped-board/low-confidence/negative samples.
- Why: False-positive and cropped-board evidence needs a schema and validator before being used for release gates.
- Files changed: scripts/validate_chess_fen_audit_dataset.py; reference_inputs/chess_fen/audit_dataset/README.md; reference_inputs/chess_fen/audit_dataset/.gitkeep; test_chess_fen_dataset_tools.py.
- Tests run:
  - `python -m unittest test_chess_fen_dataset_tools.py` -> OK.
  - `python scripts/validate_chess_fen_audit_dataset.py --help` -> OK.
- Result: Done.
- Remaining risk: The dataset structure is ready, but no real audit samples have been added.

### P1-03
- What changed: Added `scripts/export_chess_fen_square_debug_artifacts.py` to export 64 normalized square crops plus per-square piece/confidence/top-N alternatives, and added optional `--square-debug-dir` to `scripts/evaluate_chess_fen_recognizer.py`.
- Why: Square-level debug artifacts make recognition failures auditable without changing the recognizer or acceptance gates.
- Files changed: scripts/export_chess_fen_square_debug_artifacts.py; scripts/evaluate_chess_fen_recognizer.py; test_chess_fen_square_debug_artifacts.py; FEN_AUTOMATION_TASKS.md.
- Tests run:
  - `python -m unittest test_chess_fen_square_debug_artifacts.py` -> OK.
  - `python scripts/export_chess_fen_square_debug_artifacts.py --help` -> OK.
- Result: Done.
- Remaining risk: The exporter depends on `squares` already produced by recognizer template classification; if a recognition path emits no alternatives, metadata will contain empty alternatives rather than inventing them.

### P1-04
- What changed: Added `scripts/build_chess_fen_placement_review_dashboard.py` to render a static HTML/JSON placement review dashboard with crop preview, CSS grid overlay, predicted placement, expected placement, square diffs, blockers, and summary metrics.
- Why: Operators need a compact visual review surface for placement-level automation without confusing placement evidence with full-FEN acceptance.
- Files changed: scripts/build_chess_fen_placement_review_dashboard.py; test_chess_fen_placement_review_dashboard.py; FEN_AUTOMATION_TASKS.md.
- Tests run:
  - `python -m unittest test_chess_fen_placement_review_dashboard.py` -> OK.
  - `python scripts/build_chess_fen_placement_review_dashboard.py --help` -> OK.
- Result: Done.
- Remaining risk: Browser screenshot verification was not performed; this is a static generated HTML report with unit coverage for content and path hygiene.

### P2-01
- What changed: Added `scripts/evaluate_chess_fen_template_strategy.py` to decide whether the template matcher should be kept, augmented, or replaced based on recognizer evals, automation readiness, and hardened label inventory.
- Why: Avoids replacing the recognizer or adding model complexity before there is enough hardened ground-truth evidence to prove the current matcher is the bottleneck.
- Files changed: scripts/evaluate_chess_fen_template_strategy.py; test_chess_fen_template_strategy.py; FEN_AUTOMATION_TASKS.md.
- Tests run:
  - `python -m unittest test_chess_fen_template_strategy.py` -> OK.
  - `python scripts/evaluate_chess_fen_template_strategy.py --help` -> OK.
  - `python scripts/evaluate_chess_fen_template_strategy.py --recognizer-eval reports\chess_fen\evals\fundamenty_runtime_crop_self_eval.json --label-inventory reports\chess_fen\label_inventory_p1.json --output reports\chess_fen\template_strategy_p2.json` -> OK.
  - `PYTHONPYCACHEPREFIX=<temp> python -m py_compile scripts\evaluate_chess_fen_template_strategy.py test_chess_fen_template_strategy.py` -> OK.
- Result: Done. Current recommendation is `keep_template_matcher_collect_evidence`: the recognizer eval has 40 cases, `exact_fen_accuracy=0.925`, `square_accuracy=1.0`, and `false_positive_count=0`, but the hardened inventory has `0` valid human-verified labels.
- Remaining risk: The strategy is only as strong as the input reports. Current data does not justify model replacement; it justifies label/evidence hardening and false-positive/cropped-board audit collection.

## Final summary
- Completed tasks: P0-00 through P0-12, P1-01, P1-02, P1-03, P1-04, P2-01.
- Skipped tasks: none from the current execution board.
- Test results:
  - Baseline: `python -m unittest test_chess_fen_pipeline_hardening.py test_chess_fen_ml_acceptance.py test_chess_auto_flow.py` -> 39 tests OK.
  - Baseline long suite: `python -m unittest test_chess_fen_recognition.py` -> timed out after 304 seconds.
  - Recognizer benchmark: 40/40 exact FEN, 0 false positives.
  - Final targeted: 59 tests OK.
  - Py compile: OK for changed modules/scripts/tests.
  - P1 dataset tools: `python -m unittest test_chess_fen_dataset_tools.py` -> OK.
  - P1 square debug: `python -m unittest test_chess_fen_square_debug_artifacts.py` -> OK.
  - P1 placement dashboard: `python -m unittest test_chess_fen_placement_review_dashboard.py` -> OK.
  - P2 template strategy: `python -m unittest test_chess_fen_template_strategy.py` -> OK.
- Exact files changed:
  - FEN_AUTOMATION_TASKS.md
  - chess_fen_hardening.py
  - chess_auto_flow.py
  - chess_position_recognizer.py
  - scripts/chess_diagram_detection.py
  - scripts/evaluate_fen_automation_readiness.py
  - scripts/evaluate_chess_fen_label_inventory.py
  - scripts/validate_chess_fen_audit_dataset.py
  - scripts/export_chess_fen_square_debug_artifacts.py
  - scripts/build_chess_fen_placement_review_dashboard.py
  - scripts/evaluate_chess_fen_template_strategy.py
  - reference_inputs/chess_fen/audit_dataset/README.md
  - reference_inputs/chess_fen/audit_dataset/.gitkeep
  - test_chess_fen_pipeline_hardening.py
  - test_chess_auto_flow.py
  - test_chess_fen_recognition.py
  - test_chess_diagram_detection.py
  - test_fen_automation_readiness.py
  - test_chess_fen_dataset_tools.py
  - test_chess_fen_square_debug_artifacts.py
  - test_chess_fen_placement_review_dashboard.py
  - test_chess_fen_template_strategy.py
- Known risks:
  - Placement-level acceptance proves visual placement structure/plausibility, not full legal FEN metadata.
  - Full `test_chess_fen_recognition.py` is slow and timed out in this environment; targeted tests cover changed behavior.
  - Board detection quality artifact reports crop presence/status, not human-verified crop correctness.
  - Inventory reports 0 valid human-verified labels under the hardened contract, so release/corpus profile gates remain blocked until labels are migrated or reverified.
  - Audit dataset schema exists, but real false-positive/cropped-board samples still need to be collected.
  - Square debug alternatives are only as complete as recognizer-provided `squares`.
  - Placement dashboard has automated content tests but no browser screenshot evidence in this run.
  - Template strategy report recommends keeping the current matcher and collecting evidence because there are 0 valid hardened human-verified labels; recognizer replacement would be premature.
- Next recommended tasks:
  - Run the new readiness evaluator on a fresh auto chess output directory.
  - Add UI/report dashboard rendering for placement/full-FEN split.
  - Build ground-truth crop/grid labels to convert diagnostic crop quality into verified crop correctness.
  - Migrate or reverify existing label files to the full human evidence contract.
  - Run the placement dashboard on a fresh auto-output directory and inspect browser screenshots.
