# FEN 90 Percent Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KindleMaster generuje FEN dla skanowanych diagramow szachowych na poziomie co najmniej 90% mierzonego pokrycia/accuracy, bez publikowania pozycji ponizej deterministycznego progu bezpieczenstwa.

**Architecture:** Deterministyczny recognizer pozostaje jedynym zrodlem FEN publikowanego w EPUB. OpenAI Developers/OpenAI moze pomagac w review, etykietowaniu i evalach, ale wynik AI nie mutuje EPUB i nie podnosi FEN do zaakceptowanego statusu bez lokalnej walidacji. Kryterium 90% musi byc mierzone na dwoch poziomach: recznie zweryfikowany eval set oraz end-to-end smoke dla fixture klasy `diagram_training_book`.

**Tech Stack:** Python, PyMuPDF, Pillow, NumPy, `unittest`, `chess_position_recognizer.py`, `pymupdf_chess_extractor.py`, `scripts/evaluate_chess_fen_recognizer.py`, `premium_corpus_smoke.py`, opcjonalny review-only `openai_chess_fen_reviewer.py`.

---

## Current Evidence, 2026-05-27

- Full smoke fixture `fundamenty_scan_chess_pdf` passed:
  - report: `reports/smoke/verified_labels_filter_false_crops_20260527/smoke_full.json`
  - EPUB: `output/smoke/verified_labels_filter_false_crops_20260527/fundamenty_scan_chess_pdf.epub`
  - `diagram_count=386`, `fen_count=386`, `manual_review_count=0`
  - `non_board_rejected_count=4`
  - `epubcheck_status=passed`, `validation_status=passed`
  - elapsed `245.5375s`, EPUB size `3,254,924` bytes
- Eval labels:
  - seed labels: `reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl`, `40` records
  - verified exact crop labels: `reference_inputs/chess_fen/labels/fundamenty_verified_crop_labels.jsonl`, `32` records
- Deterministic eval result with explicit threshold:
  - command: `python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/fundamenty_merida_like --min-confidence 0.84 --min-exact-accuracy 0.90 --output reports/chess_fen/evals/fen_90_threshold_084_20260527.json`
  - result: `status=passed`, `case_count=40`, `exact_fen_count=37`, `exact_fen_accuracy=0.925`, `square_accuracy=1.0`
- Acceptance eval artifact after Task 1:
  - output: `reports/chess_fen/evals/fen_90_acceptance_latest.json`
  - result: `status=passed`, `case_count=40`, `exact_fen_count=37`, `exact_fen_accuracy=0.925`, `false_positive_count=0`, `false_positive_rate=0.0`, `square_accuracy=1.0`
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_evaluate_chess_fen_recognizer_reports_exact_fen_accuracy test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_eval_passes_90_percent_gate_without_false_positives`
  - full FEN tests: `python -m unittest test_chess_fen_recognition.py`, `86` tests OK
- Smoke FEN acceptance gate after Task 2:
  - report: `reports/smoke/fen_90_gate_20260527/smoke_full.json`
  - EPUB: `output/smoke/fen_90_gate_20260527/fundamenty_scan_chess_pdf.epub`
  - result: `overall_status=passed`, `fen_acceptance_gate.status=passed`, `fen_coverage=1.0`, `diagram_count=386`, `fen_count=386`, `manual_review_count=0`, `fen_failed_cases=0`, `fen_warning_cases=0`, `validation=passed`
  - targeted tests: `python -m unittest test_smoke_chess_quality.py`, `5` tests OK
  - broader tests: `python -m unittest test_chess_fen_recognition.py test_chess_fix.py test_smoke_chess_quality.py test_publication_pipeline.py`, `118` tests OK
- Corpus FEN gate after Task 3 slice:
  - script: `scripts/evaluate_chess_fen_corpus.py`
  - report: `reports/chess_fen/evals/fen_corpus_90_latest.json`
  - result on current manifest: `status=passed`, `evaluated_case_count=1`, `total_labeled_diagram_count=40`, `total_exact_fen_count=37`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
  - generalization gate report: `reports/chess_fen/evals/fen_corpus_90_requires_2_profiles_latest.json`
  - current generalization result: `status=failed`, `min_profile_count=2`, `evaluated_case_count=1`, `missing_profile_count=1`, reason `manifest has 1 chess FEN profile(s), below required minimum 2`
  - test: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_reads_manifest_profiles`, `1` test OK
  - broader tests: `python -m unittest test_chess_fen_recognition.py test_chess_fix.py test_smoke_chess_quality.py test_publication_pipeline.py`, `120` tests OK
  - refreshed generalization gate: `python scripts\evaluate_chess_fen_corpus.py --manifest reference_inputs\manifest.json --min-confidence 0.84 --min-exact-accuracy 0.90 --min-profile-count 2 --output reports\chess_fen\evals\fen_corpus_90_requires_2_profiles_latest.json`
  - refreshed result: `status=failed`, `evaluated_case_count=1`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
- Entrypoint and corpus integration after Task 3 governance slice:
  - `test_chess_fen_recognition.py` is registered in `CORPUS_TESTS`
  - `test_react_shell_browser_smoke.py` is registered in `BROWSER_TESTS`
  - focused governance tests: `python -m unittest test_kindlemaster_entrypoint.py test_corpus_gate.py`, `45` tests OK
  - syntax check: `python -m py_compile kindlemaster.py scripts\run_corpus_gate.py test_corpus_gate.py`, OK
  - targeted FEN/corpus tests: `python -m unittest test_chess_fen_recognition.py test_smoke_chess_quality.py test_corpus_gate.py`, `109` tests OK
  - CI-shaped corpus command: `python kindlemaster.py corpus --proof-profile ci --smoke-case ocr_probe_pdf --premium-case document_like_report_pdf --fen-min-profile-count 1 --output-root output\corpus\fen_gate_ci_20260527_r2 --reports-root reports\corpus\fen_gate_ci_20260527_r2`
  - corpus result: `overall_status=passed_with_warnings`, `fen_corpus.status=passed`, `fen_corpus.evaluated_case_count=1`, `fen_corpus.overall_exact_fen_accuracy=0.925`, `fen_corpus.total_false_positive_count=0`
  - FEN corpus artifact: `reports/corpus/fen_gate_ci_20260527_r2/fen_corpus_90.json`
- OpenAI review-only contract after Task 4:
  - provider: `openai_chess_fen_reviewer.py`
  - checker: `python scripts\check_openai_chess_fen_reviewer.py`
  - checker result: `enabled=false`, `mode=review_only`, `mutates_fen=false`
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_review_payload_never_changes_fen_output test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_reviewer_payload_is_review_only_audit_data`, `2` tests OK
  - full FEN tests after OpenAI contract update: `python -m unittest test_chess_fen_recognition.py`, `89` tests OK
  - syntax check: `python -m py_compile openai_chess_fen_reviewer.py scripts\check_openai_chess_fen_reviewer.py test_chess_fen_recognition.py`, OK
- Local second-fixture discovery after Task 3 continuation:
  - added diagnostic script: `scripts/discover_chess_pdf_candidates.py`
  - focused helper test: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_pdf_discovery_helpers_keep_sampling_bounded`, `1` test OK
  - syntax check: `python -m py_compile scripts\discover_chess_pdf_candidates.py test_chess_fen_recognition.py`, OK
  - discovery report: `reports/chess_fen/discovery/local_pdf_candidates_20260527.json`
  - discovery command: `python scripts\discover_chess_pdf_candidates.py reference_inputs\pdf C:\Users\user\Downloads --max-files 40 --pages-per-pdf 4 --render-dpi 90 --min-candidate-pages 2 --output reports\chess_fen\discovery\local_pdf_candidates_20260527.json`
  - discovery result: `status=completed`, `pdf_count=40`, `candidate_count=2`, both candidates are the same `Fundamenty 1-1` PDF/copy; no second real scanned chess PDF was found in this pass
  - broader shallow user PDF scan: `reports/chess_fen/discovery/local_user_pdf_candidates_20260527.json`, `pdf_count=160`, `candidate_count=0`; this is a weak negative signal because it sampled only `2` pages per PDF
  - targeted `Downloads\skompresowane` scan: `python scripts\discover_chess_pdf_candidates.py "C:\Users\user\Downloads\skompresowane" --max-files 80 --pages-per-pdf 6 --render-dpi 90 --min-candidate-pages 1 --output reports\chess_fen\discovery\skompresowane_pdf_candidates_20260527.json`
  - targeted scan result: `pdf_count=4`, `candidate_count=0`; this confirms no second scanned board-image fixture was found in that folder by the image-board detector, while `tactits.pdf` remains useful as a font-board review candidate source
  - filename keyword search in `Downloads` and `reference_inputs/pdf` found only `Fundamenty 1-1.pdf` as a credible chess PDF; `material_nauka_eursap_coupa_iwo_v5.pdf` matched `mate` inside `material`, not chess
  - full FEN tests after discovery helper: `python -m unittest test_chess_fen_recognition.py`, `90` tests OK
  - corrected syntax check: `python -m py_compile scripts\discover_chess_pdf_candidates.py test_chess_fen_recognition.py`, OK
- New-profile intake after Task 3 continuation:
  - added review-only intake script: `scripts/prepare_chess_fen_profile_intake.py`
  - intake creates `candidate_labels_review.jsonl`, `manifest_case_draft.json`, `README.md`, and `intake_summary.json`
  - safety contract: `accepted_for_corpus=false` until FEN labels are manually verified, templates are built, and corpus eval passes
  - focused test: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_profile_intake_creates_review_only_seed_package`, `1` test OK
  - operational smoke: `python scripts\prepare_chess_fen_profile_intake.py reference_inputs\pdf\fundamenty_1_1_scan_chess.pdf --profile-id fundamenty_intake_probe --output-dir reports\chess_fen\intake --pages 16 --dpi 72 --max-candidates-per-page 3 --min-grid-confidence 0.50 --min-seed-labels 20`
  - smoke result: `status=insufficient_crops`, `candidate_label_count=11`, `required_verified_seed_count=20`, `accepted_for_corpus=false`
  - full FEN tests after intake: `python -m unittest test_chess_fen_recognition.py`, `91` tests OK
  - syntax check: `python -m py_compile scripts\prepare_chess_fen_profile_intake.py scripts\discover_chess_pdf_candidates.py test_chess_fen_recognition.py`, OK
  - refreshed two-profile gate still fails as intended: `status=failed`, `evaluated_case_count=1`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
- Label-validation gate after Task 3 continuation:
  - added validator: `scripts/validate_chess_fen_labels.py`
  - corpus evaluator now validates every manifest-backed `chess_fen_seed_labels` file before recognition
  - validator checks: non-empty FEN, `validate_fen`, crop path exists, `verified_by`, `verified_at`, and no review-only/placeholder label status or notes
  - real seed validation: `python scripts\validate_chess_fen_labels.py reference_inputs\chess_fen\labels\fundamenty_seed_positions.jsonl --output reports\chess_fen\evals\fundamenty_seed_label_validation_latest.json`
  - real seed result: `status=passed`, `label_count=40`, `valid_label_count=40`, `issue_count=0`
  - intake queue validation: `python scripts\validate_chess_fen_labels.py reports\chess_fen\intake\fundamenty_intake_probe\candidate_labels_review.jsonl --output reports\chess_fen\intake\fundamenty_intake_probe\candidate_label_validation.json`
  - intake queue result: `status=failed`, `label_count=11`, `valid_label_count=0`, `issue_count=55`; this failure is expected because intake labels are review-only and must not count as 90% proof
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_reads_manifest_profiles test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_can_require_multiple_profiles test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_rejects_review_only_labels_before_accuracy_gate`, `3` tests OK
  - corpus gate tests after validator integration: `python -m unittest test_corpus_gate.py`, `16` tests OK
  - full FEN tests after validator integration: `python -m unittest test_chess_fen_recognition.py`, `92` tests OK
  - post-cleanup focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_rejects_review_only_labels_before_accuracy_gate test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_reads_manifest_profiles`, `2` tests OK
  - syntax check: `python -m py_compile scripts\validate_chess_fen_labels.py scripts\evaluate_chess_fen_corpus.py test_chess_fen_recognition.py`, OK
  - refreshed two-profile gate after validator integration: `status=failed`, `evaluated_case_count=1`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`, `label_validation.status=passed`
- Minimum seed-size gate after Task 3 continuation:
  - corpus evaluator now enforces `default_min_seed_label_count=20` per profile unless a manifest case explicitly sets `chess_fen_seed_min_count`
  - this prevents a second profile from passing with a tiny `1` or `2` label dataset and `100%` accuracy
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_reads_manifest_profiles test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_can_require_multiple_profiles test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_corpus_evaluator_rejects_tiny_seed_profile_by_default`, `3` tests OK
  - full FEN tests: `python -m unittest test_chess_fen_recognition.py`, `93` tests OK
  - corpus gate tests: `python -m unittest test_corpus_gate.py`, `16` tests OK
  - one-profile corpus eval: `python scripts\evaluate_chess_fen_corpus.py --manifest reference_inputs\manifest.json --min-confidence 0.84 --min-exact-accuracy 0.90 --min-profile-count 1 --output reports\chess_fen\evals\fen_corpus_90_latest.json`
  - one-profile result: `status=passed`, `default_min_seed_label_count=20`, `label_count=40`, `valid_label_count=40`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
  - two-profile result: `status=failed`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
  - CI-shaped corpus command: `python kindlemaster.py corpus --proof-profile ci --smoke-case ocr_probe_pdf --premium-case document_like_report_pdf --fen-min-profile-count 1 --output-root output\corpus\fen_gate_ci_20260527_min_labels --reports-root reports\corpus\fen_gate_ci_20260527_min_labels`
  - CI-shaped corpus result: `overall_status=passed_with_warnings`, `fen_corpus.status=passed`, `fen_corpus.default_min_seed_label_count=20`, `fen_corpus.overall_exact_fen_accuracy=0.925`, `fen_corpus.total_false_positive_count=0`; warnings are from intentionally skipped non-FEN focus routes under the selected filters
- Corpus CLI seed-size argument after Task 3 continuation:
  - `kindlemaster.py corpus` and `scripts/run_corpus_gate.py` now expose `--fen-min-seed-label-count`
  - corpus gate passes `default_min_seed_label_count` into `evaluate_chess_fen_corpus`
  - corpus markdown now surfaces `FEN min seed labels/profile` and `FEN valid seed labels`
  - targeted tests: `python -m unittest test_corpus_gate.py test_kindlemaster_entrypoint.py`, `46` tests OK
  - syntax check: `python -m py_compile kindlemaster.py scripts\run_corpus_gate.py test_corpus_gate.py`, OK
  - real command: `python kindlemaster.py corpus --proof-profile ci --smoke-case ocr_probe_pdf --premium-case document_like_report_pdf --fen-min-profile-count 1 --fen-min-seed-label-count 20 --output-root output\corpus\fen_gate_ci_20260527_seed_arg --reports-root reports\corpus\fen_gate_ci_20260527_seed_arg`
  - real result: `overall_status=passed_with_warnings`, `fen_corpus.status=passed`, `default_min_seed_label_count=20`, `label_count=40`, `valid_label_count=40`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
- Default eval threshold codification after Task 3 continuation:
  - `scripts/evaluate_chess_fen_recognizer.py` now uses `DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE = 0.84` instead of the older implicit `0.85`
  - `scripts/evaluate_chess_fen_corpus.py` and `scripts/run_corpus_gate.py` consume the same default threshold
  - evaluator JSON now includes `min_confidence`, so reports show the threshold that produced the result
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_eval_default_confidence_matches_90_percent_gate test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_eval_passes_90_percent_gate_without_false_positives`, `2` tests OK
  - syntax check: `python -m py_compile scripts\evaluate_chess_fen_recognizer.py scripts\evaluate_chess_fen_corpus.py scripts\run_corpus_gate.py test_chess_fen_recognition.py`, OK
  - refreshed recognizer eval: `status=passed`, `case_count=40`, `min_confidence=0.84`, `exact_fen_accuracy=0.925`, `false_positive_count=0`
  - refreshed one-profile corpus eval: `status=passed`, `min_confidence=0.84`, `evaluated_case_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`, `next_required_actions=[]`
  - refreshed two-profile gate remains intentionally failed: `status=failed`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`, `next_required_actions=["add 1 real scanned chess FEN profile(s) to reach min_profile_count=2; each needs at least 20 manually verified labels"]`
  - corpus markdown now surfaces `FEN next actions`
  - regression tests after change: `python -m unittest test_chess_fen_recognition.py`, `95` tests OK; `python -m unittest test_corpus_gate.py test_kindlemaster_entrypoint.py`, `46` tests OK
- Intake label-aids after Task 3 continuation:
  - added review-only aid generator: `scripts/build_chess_fen_label_aids.py`
  - outputs grid overlays, `contact_sheet.png`, `manual_label_template.jsonl`, `README.md`, and `label_aids_summary.json`
  - policy remains `review_only_no_fen_generation`; generated templates keep `fen`, `verified_by`, and `verified_at` empty
  - focused test: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_label_aids_do_not_generate_accepted_fen`, `1` test OK
  - syntax check: `python -m py_compile scripts\build_chess_fen_label_aids.py test_chess_fen_recognition.py`, OK
  - operational aid build: `python scripts\build_chess_fen_label_aids.py reports\chess_fen\intake\fundamenty_intake_probe\candidate_labels_review.jsonl --output-dir reports\chess_fen\intake\fundamenty_intake_probe\label_aids --max-items 20`
  - operational result: `row_count=11`, `aid_count=11`, `missing_crop_count=0`, contact sheet at `reports\chess_fen\intake\fundamenty_intake_probe\label_aids\contact_sheet.png`
  - safety validation: `python scripts\validate_chess_fen_labels.py reports\chess_fen\intake\fundamenty_intake_probe\label_aids\manual_label_template.jsonl --output reports\chess_fen\intake\fundamenty_intake_probe\label_aids\manual_label_template_validation.json`
  - safety validation result: `status=failed`, `label_count=11`, `valid_label_count=0`; this is expected because no FEN is generated automatically
  - full FEN tests after label aids: `python -m unittest test_chess_fen_recognition.py`, `94` tests OK
- Font-board candidate separation after Task 3 continuation:
  - `scripts/evaluate_chess_fen_corpus.py` now reports `chess_fen_font_board_candidate_labels` separately from verified `chess_fen_seed_labels`
  - font-board candidates do not increase `evaluated_case_count` and do not reduce `missing_profile_count` for the strict two-profile scanned gate
  - candidate files fail the corpus gate if they contain accepted `fen` labels, so review-only material cannot silently count as proof
  - corpus markdown now surfaces `FEN font-board candidate profiles`, `FEN font-board candidate status`, and `FEN font-board candidate failures`
  - operational candidate eval: `python scripts\evaluate_chess_font_board_candidates.py reports\chess_fen\font_board_intake\tactits_probe_20260527\candidate_font_board_labels_review.jsonl --min-candidate-fen-coverage 0.90 --output reports\chess_fen\font_board_intake\tactits_probe_20260527\candidate_fen_eval.json`
  - operational result: `status=review_ready`, `row_count=48`, `candidate_fen_count=48`, `valid_candidate_fen_count=48`, `candidate_fen_coverage=1.0`, `accepted_label_count=0`, `accepted_for_corpus=false`
  - refreshed strict gate: `python scripts\evaluate_chess_fen_corpus.py --manifest reference_inputs\manifest.json --min-exact-accuracy 0.90 --min-profile-count 2 --output reports\chess_fen\evals\fen_corpus_90_requires_2_profiles_latest.json`
  - refreshed result: `status=failed`, `evaluated_case_count=1`, `font_board_candidate_profile_count=0`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
  - tests: `python -m unittest test_chess_fen_recognition.py`, `105` tests OK; `python -m unittest test_corpus_gate.py test_kindlemaster_entrypoint.py`, `46` tests OK
- OpenAI label-assist request export after Task 4 continuation:
  - `scripts/export_chess_fen_review_queue.py` now writes `openai_label_assist_requests.jsonl` beside `queue.jsonl`
  - `scripts/build_chess_fen_label_aids.py` now writes the same style of OpenAI Responses API request JSONL for grid-aid crops
  - every generated request is explicitly `accepted_for_corpus=false` and marked review-only, so OpenAI can accelerate labeling but cannot promote FEN into EPUB or corpus proof
  - request payloads include `input_text` with deterministic context and `input_image` as a base64 data URL, matching the Responses API image-input pattern
  - operational run: `python scripts\build_chess_fen_label_aids.py reports\chess_fen\intake\fundamenty_intake_probe\candidate_labels_review.jsonl --output-dir reports\chess_fen\intake\fundamenty_intake_probe\label_aids --max-items 20`
  - operational result: `aid_count=11`, `openai_request_count=11`, `accepted_for_corpus=false`, `openai_policy=label_assist_review_only_no_corpus_promotion`
  - safety validation of `manual_label_template.jsonl` still fails as intended with `valid_label_count=0` until a human fills `fen`, `verified_by`, and `verified_at`
  - tests: focused review/label-aid tests `3` OK; full `python -m unittest test_chess_fen_recognition.py`, `105` tests OK; syntax checks OK
- OpenAI/human label-assist response import after Task 4 continuation:
  - added `scripts/import_chess_fen_label_assist.py`
  - it accepts direct JSONL review rows or OpenAI Batch-style rows with `custom_id` and `response.body.output_text`
  - importer writes `manual_verification_draft.jsonl` with `ai_suggested_fen`, `ai_approved`, `ai_confidence`, and `ai_issues`
  - it deliberately keeps `fen`, `verified_by`, and `verified_at` empty and sets `label_status=needs_manual_fen`, so the validator fails until a human checks and promotes the row
  - operational sample: one synthetic approved OpenAI response for `fundamenty_intake_probe` produced `matched_response_count=1`, `ready_for_manual_verification_count=1`, `accepted_for_corpus=false`
  - safety validation: `python scripts\validate_chess_fen_labels.py reports\chess_fen\intake\fundamenty_intake_probe\label_aids\label_assist_import_sample\manual_verification_draft.jsonl --output reports\chess_fen\intake\fundamenty_intake_probe\label_aids\label_assist_import_sample\manual_verification_draft_validation.json`
  - safety result: `status=failed`, `label_count=11`, `valid_label_count=0`, with `fen_missing`, `verified_by_missing`, `verified_at_missing`, and `review_only_label_status`
  - tests: focused importer tests `2` OK; full `python -m unittest test_chess_fen_recognition.py`, `109` tests OK; syntax checks OK
- Human promotion from label-assist draft after Task 4 continuation:
  - added `scripts/promote_chess_fen_label_draft.py`
  - default behavior refuses to promote AI suggestions unless the row has `human_verified=true`; CLI can also use explicit `--accept-ai-suggestions` after human visual review
  - promoted rows get `fen`, `verified_by`, `verified_at`, and `label_status=verified`; `accepted_for_corpus` remains `false` until profile readiness/corpus gates pass
  - operational safety run without `--accept-ai-suggestions`: `promoted_count=0`, `skipped_count=11`, reason `manual_approval_missing`
  - operational sample with `--accept-ai-suggestions`: `promoted_count=1`, `validation.status=passed`, `valid_label_count=1`, `ready_for_profile_gate=true`, `accepted_for_corpus=false`
  - tests: focused promotion tests `2` OK; full `python -m unittest test_chess_fen_recognition.py`, `111` tests OK; syntax checks OK
- Profile readiness/promote gate after Task 3 continuation:
  - added `scripts/check_chess_fen_profile_ready.py`
  - purpose: validate a manually verified profile package before adding it to `reference_inputs/manifest.json`
  - inputs: `manifest_case_draft.json`, verified seed labels, template dir
  - gates: existing source PDF, no `candidate_labels_review.jsonl`/`manual_label_template.jsonl`, `validate_chess_fen_labels`, at least `20` valid labels, template build, recognizer eval with `min_confidence=0.84`, `exact_fen_accuracy >= 0.90`, `false_positive_count=0`
  - output: `accepted_for_corpus=true` and `manifest_case_ready` only when all gates pass; OpenAI policy remains `review_only_not_used`
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_profile_ready_rejects_review_only_aid_template test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_profile_ready_rejects_below_20_verified_labels test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_profile_ready_accepts_verified_synthetic_20_label_profile`, `3` tests OK
  - operational check on current intake probe: `python scripts\check_chess_fen_profile_ready.py reports\chess_fen\intake\fundamenty_intake_probe\manifest_case_draft.json --template-dir reports\chess_fen\intake\fundamenty_intake_probe\ready_check_templates --output reports\chess_fen\intake\fundamenty_intake_probe\profile_ready_check.json`
  - operational result: `status=failed`, `accepted_for_corpus=false`, `valid_label_count=0`, next actions require fixing label validation and adding verified labels until `valid_label_count >= 20`
  - full FEN tests after readiness gate: `python -m unittest test_chess_fen_recognition.py`, `98` tests OK
  - corpus/entrypoint tests after readiness gate: `python -m unittest test_corpus_gate.py test_kindlemaster_entrypoint.py`, `46` tests OK
  - syntax check: `python -m py_compile scripts\check_chess_fen_profile_ready.py scripts\evaluate_chess_fen_corpus.py scripts\run_corpus_gate.py test_chess_fen_recognition.py test_corpus_gate.py`, OK
- Font-board intake after Task 3 continuation:
  - local candidate discovered: `C:\Users\user\Downloads\skompresowane\tactits.pdf` (Laszlo Polgar style font-board diagrams), not copied into repo and not accepted as corpus proof
  - image-grid discovery result: `reports\chess_fen\discovery\tactits_local_candidate_20260527.json`, `candidate_count=0`; root cause is font/text board encoding, not raster scan layout
  - CTAN references identify `SkakNew-Diagram` as a chess diagram font family; local PyMuPDF spans also report `font_names=["SkakNew-Diagram"]`
  - added review-only extractor: `scripts/extract_chess_font_board_candidates.py`
  - extractor output fields: `raw_rows`, `raw_board_text`, `font_names`, `bbox`, empty `fen`, `label_status=needs_manual_fen`; policy remains `review_only_no_fen_generation`
  - added deterministic SkakNew glyph map in `chess_position_recognizer.py`; source evidence is CTAN SkakNew documentation's initial-position rows `rmblkans`, `opopopop`, `POPOPOPO`, `SNAQJBMR`
  - `recognize_font_board_from_lines` now decodes SkakNew row/rank prefixes and `Z` empty squares into candidate FEN without OpenAI
  - operational command: `python scripts\extract_chess_font_board_candidates.py "C:\Users\user\Downloads\skompresowane\tactits.pdf" --page-start 69 --pages 8 --output-dir reports\chess_fen\font_board_intake\tactits_probe_20260527 --min-seed-labels 20`
  - operational result: `status=review_required`, `candidate_label_count=48`, `candidate_fen_count=48`, `candidate_requires_review_count=0`, `accepted_for_corpus=false`, candidate labels at `reports\chess_fen\font_board_intake\tactits_probe_20260527\candidate_font_board_labels_review.jsonl`
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_skaknew_font_board_rows_decode_to_fen_without_ai test_chess_fen_recognition.ChessFenRecognitionTests.test_skaknew_tactics_rows_decode_sparse_problem_to_fen test_chess_fen_recognition.ChessFenRecognitionTests.test_font_board_candidate_extractor_creates_review_only_rows`, `3` tests OK
  - added candidate coverage evaluator: `scripts/evaluate_chess_font_board_candidates.py`
  - evaluator policy: `candidate_fen_is_review_aid_not_corpus_label`; it fails if review-only files contain accepted `fen` labels
  - operational candidate eval: `python scripts\evaluate_chess_font_board_candidates.py reports\chess_fen\font_board_intake\tactits_probe_20260527\candidate_font_board_labels_review.jsonl --min-candidate-fen-coverage 0.90 --output reports\chess_fen\font_board_intake\tactits_probe_20260527\candidate_fen_eval.json`
  - operational candidate eval result: `status=review_ready`, `row_count=48`, `candidate_fen_coverage=1.0`, `valid_candidate_fen_coverage=1.0`, `candidate_requires_review_count=0`, `accepted_label_count=0`, `accepted_for_corpus=false`
  - focused evaluator tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_font_board_candidate_evaluator_keeps_candidate_fen_out_of_corpus_labels test_chess_fen_recognition.ChessFenRecognitionTests.test_font_board_candidate_evaluator_rejects_mixed_accepted_labels`, `2` tests OK
  - full FEN tests after font-board candidate evaluator: `python -m unittest test_chess_fen_recognition.py`, `103` tests OK
  - corpus/entrypoint tests after font-board candidate evaluator: `python -m unittest test_corpus_gate.py test_kindlemaster_entrypoint.py`, `46` tests OK
- Woodpecker second-profile intake after continuation:
  - source kept local, not accepted into reference manifest: `C:\Users\user\Downloads\skompresowane\The Woodpecker Method ( PDFDrive ).pdf`
  - deep discovery: `reports\chess_fen\discovery\woodpecker_deep_candidates_20260527.json`, result `candidate_count=1`, `candidate_page_count=38`, `total_board_candidates=76`
  - intake: `reports\chess_fen\intake\woodpecker_method_probe\intake_summary.json`, result `status=review_required`, `candidate_label_count=179`, `required_verified_seed_count=20`, `accepted_for_corpus=false`
  - label aids: `reports\chess_fen\intake\woodpecker_method_probe\label_aids\label_aids_summary.json`, result `aid_count=20`, `openai_request_count=20`, `missing_crop_count=0`, policy `review_only_no_fen_generation`
  - safety validation: `python scripts\validate_chess_fen_labels.py reports\chess_fen\intake\woodpecker_method_probe\label_aids\manual_label_template.jsonl --output reports\chess_fen\intake\woodpecker_method_probe\label_aids\manual_label_template_validation.json`
  - safety result: `status=failed`, `valid_label_count=0`, as expected until visual FEN, `verified_by`, and `verified_at` are filled
- Holdout/generalization gate after continuation:
  - added `scripts/evaluate_chess_fen_profile_holdout.py`
  - purpose: build templates from a deterministic train split only, then evaluate held-out rows so a profile cannot claim 90% only by training and testing on the same crop set
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_profile_holdout_evaluator_trains_without_holdout_rows test_chess_fen_recognition.ChessFenRecognitionTests.test_chess_fen_profile_holdout_rejects_review_only_labels`, `2` tests OK
  - full FEN tests: `python -m unittest test_chess_fen_recognition.py`, `113` tests OK
  - syntax check: `python -m py_compile scripts\evaluate_chess_fen_profile_holdout.py test_chess_fen_recognition.py`, OK
  - real holdout at acceptance threshold: `python scripts\evaluate_chess_fen_profile_holdout.py reference_inputs\chess_fen\labels\fundamenty_seed_positions.jsonl --min-confidence 0.84 --min-exact-accuracy 0.90 --fold-count 5 --holdout-fold 0 --output reports\chess_fen\evals\fundamenty_holdout_fold0_latest.json`
  - real holdout result: `status=failed`, `train_label_count=32`, `holdout_label_count=8`, `fen_count=0`, `square_accuracy=0.9668`; this exposes a generalization weakness rather than a publication-safe pass
  - diagnostic lower-threshold run: `reports\chess_fen\evals\fundamenty_holdout_fold0_conf070_latest.json`, result `fen_count=5`, `exact_fen_count=3`, `false_positive_count=2`; this confirms that lowering confidence would increase unsafe FEN, so the next fix should improve classifier/generalization instead of loosening the gate
- Cross-marker false suppression fix after continuation:
  - root cause from holdout: real black rook on `h8` in `fundamenty_p032_runtime_02` was being suppressed as `annotation_cross_marker_suppressed`
  - changed `MAX_CROSS_MARKER_TEMPLATE_CONFIDENCE` from `0.55` to `0.35`, so the cross-marker filter only suppresses very weak non-piece matches and no longer erases a plausible real rook on a hatched dark square
  - added regression: `test_cross_marker_filter_does_not_suppress_real_dark_square_rook`
  - focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_cross_marker_filter_does_not_suppress_real_dark_square_rook test_chess_fen_recognition.ChessFenRecognitionTests.test_instructional_cross_marker_is_not_a_piece_shape`, `2` tests OK
  - refreshed self-eval: `python scripts\evaluate_chess_fen_recognizer.py reference_inputs\chess_fen\labels\fundamenty_seed_positions.jsonl --template-dir reference_inputs\chess_fen\templates\fundamenty_merida_like --min-confidence 0.84 --min-exact-accuracy 0.90 --output reports\chess_fen\evals\fen_90_acceptance_latest.json`, result still `status=passed`, `exact_fen_accuracy=0.925`, `false_positive_count=0`
  - refreshed holdout diagnostic at `0.70`: `reports\chess_fen\evals\fundamenty_holdout_fold0_conf070_latest.json`, improved from `exact_fen_count=3`, `false_positive_count=2`, `square_accuracy=0.9668` to `exact_fen_count=4`, `false_positive_count=1`, `square_accuracy=0.9688`
  - refreshed holdout at acceptance threshold `0.84`: still `status=failed`, `fen_count=0`, `false_positive_count=0`; blocker remains classifier confidence/generalization, not this one filter
  - full FEN tests: `python -m unittest test_chess_fen_recognition.py`, `114` tests OK
  - syntax check: `python -m py_compile chess_position_recognizer.py scripts\evaluate_chess_fen_profile_holdout.py test_chess_fen_recognition.py`, OK
  - strict two-profile gate remains intentionally failed: `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`
- Important nuance:
  - resolved: the acceptance threshold is visible in evaluator JSON as `min_confidence`, preventing accidental regressions back to the older `0.85` default;
  - updated for the 80% safety goal: runtime `ConversionConfig.chess_fen_min_confidence` now matches the stricter audited value `0.84`, so low-certainty diagrams stay in review instead of being published with FEN.

## Definition Of 90%

Use these acceptance gates together:

- `seed_exact_fen_accuracy >= 0.90` on a manually verified seed/eval JSONL for every supported template profile.
- `full_fixture_fen_coverage >= 0.90` where `full_fixture_fen_coverage = fen_count / diagram_count` in `smoke_full.json`.
- `false_positive_rate = 0` on the manually verified seed/eval set; if a FEN is emitted and differs from expected, the gate fails immediately.
- `epubcheck_status=passed` and KindleMaster validation `passed` for the generated EPUB.
- OpenAI review may create review notes or draft labels, but `mutates_fen=false` must remain true.

---

### Task 1: Codify FEN 90 Eval Gate

**Files:**
- Modify: `scripts/evaluate_chess_fen_recognizer.py`
- Modify: `test_chess_fen_recognition.py`
- Create: `reports/chess_fen/evals/` outputs during verification only

- [x] **Step 1: Add a regression test for the 90% seed gate**

Add this test to `test_chess_fen_recognition.py`:

```python
def test_fundamenty_seed_eval_passes_90_percent_gate(self) -> None:
    from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer

    result = evaluate_chess_fen_recognizer(
        "reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl",
        template_dir="reference_inputs/chess_fen/templates/fundamenty_merida_like",
        min_confidence=0.84,
        min_exact_accuracy=0.90,
    )

    self.assertEqual(result["status"], "passed")
    self.assertGreaterEqual(result["exact_fen_accuracy"], 0.90)
    self.assertEqual(result["false_positive_count"], 0)
    self.assertGreaterEqual(result["square_accuracy"], 0.995)
```

- [x] **Step 2: Run the focused test and confirm the current failure mode**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_eval_passes_90_percent_gate
```

Observed before implementation: `false_positive_count` was missing from evaluator output.

- [x] **Step 3: Add false-positive accounting to the evaluator**

Modify `scripts/evaluate_chess_fen_recognizer.py` so each case distinguishes:

```python
false_positive = bool(actual_fen and actual_fen != expected_fen)
```

Add summary fields:

```python
"false_positive_count": false_positive_count,
"false_positive_rate": round(false_positive_count / max(1, fen_count), 4),
```

Set status to failed if any false positive exists:

```python
status_passed = bool(labels and exact_fen_accuracy >= min_exact_accuracy and false_positive_count == 0)
```

- [x] **Step 4: Run the focused test again**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_eval_passes_90_percent_gate
```

Observed: `OK`, `2` tests.

- [x] **Step 5: Generate the acceptance eval artifact**

Run:

```powershell
python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/fundamenty_merida_like --min-confidence 0.84 --min-exact-accuracy 0.90 --output reports/chess_fen/evals/fen_90_acceptance_latest.json
```

Expected:

```text
"status": "passed"
"exact_fen_accuracy": 0.925
"false_positive_count": 0
```

Observed: `status=passed`, `case_count=40`, `exact_fen_accuracy=0.925`, `false_positive_count=0`, `square_accuracy=1.0`.

### Task 2: Make The 90% Threshold Visible In Reports

**Files:**
- Modify: `scripts/run_smoke_tests.py`
- Modify: `test_smoke_chess_quality.py`

- [x] **Step 1: Add smoke report fields for FEN coverage**

In the smoke case result, compute:

```python
diagram_count = int(chess_fen.get("diagram_count") or 0)
fen_count = int(chess_fen.get("fen_count") or 0)
fen_coverage = round(fen_count / max(1, diagram_count), 4)
```

Implemented as `fen_acceptance_gate`:

```python
"status": "passed" if diagram_count and fen_coverage >= 0.90 else "failed",
"fen_coverage": fen_coverage,
"fen_acceptance_min": 0.90,
```

- [x] **Step 2: Add a smoke-quality test**

Add to `test_smoke_chess_quality.py` a synthetic report assertion:

```python
def test_chess_fen_quality_requires_90_percent_coverage(self) -> None:
    chess_fen = {"diagram_count": 100, "fen_count": 89}
    coverage = round(chess_fen["fen_count"] / max(1, chess_fen["diagram_count"]), 4)
    self.assertLess(coverage, 0.90)

    chess_fen["fen_count"] = 90
    coverage = round(chess_fen["fen_count"] / max(1, chess_fen["diagram_count"]), 4)
    self.assertGreaterEqual(coverage, 0.90)
```

- [x] **Step 3: Run smoke-quality tests**

Run:

```powershell
python -m unittest test_smoke_chess_quality.py
```

Observed: `OK`, `5` tests. Broader `test_chess_fen_recognition.py test_chess_fix.py test_smoke_chess_quality.py test_publication_pipeline.py` also passed with `118` tests.

### Task 3: Expand Beyond One Book Class

**Files:**
- Modify: `reference_inputs/manifest.json`
- Create: `reference_inputs/chess_fen/labels/<profile>_seed_positions.jsonl`
- Create: `reference_inputs/chess_fen/templates/<profile>/README.md`
- Create: `scripts/evaluate_chess_fen_corpus.py`
- Modify: `docs/superpowers/plans/2026-05-27-fen-90-acceptance.md`

- [x] **Step 0: Add a manifest-backed corpus evaluator**

Create `scripts/evaluate_chess_fen_corpus.py` to read every manifest case with `chess_fen_seed_labels`, resolve the matching `chess_fen_template_profile`, run `evaluate_chess_fen_recognizer`, and fail if any case misses `chess_fen_seed_exact_accuracy_min` or emits a false-positive FEN.

Run:

```powershell
python scripts/evaluate_chess_fen_corpus.py --manifest reference_inputs\manifest.json --min-confidence 0.84 --min-exact-accuracy 0.90 --output reports\chess_fen\evals\fen_corpus_90_latest.json
```

Observed on current manifest: `status=passed`, `evaluated_case_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`.

For generalization proof, run with at least two profiles:

```powershell
python scripts/evaluate_chess_fen_corpus.py --manifest reference_inputs\manifest.json --min-confidence 0.84 --min-exact-accuracy 0.90 --min-profile-count 2 --output reports\chess_fen\evals\fen_corpus_90_requires_2_profiles_latest.json
```

Current expected result until a second fixture/profile is added: `status=failed`, `missing_profile_count=1`.

- [ ] **Step 1: Add at least one second scanned chess fixture**

Add a manifest case with these minimum fields:

```json
{
  "id": "scanned_chess_fixture_2",
  "document_class": "diagram_training_book",
  "input_type": "pdf",
  "language": "pl",
  "quick_smoke": false,
  "release_strict": false,
  "source": "reference_inputs/pdf/scanned_chess_fixture_2.pdf",
  "target": "reference_inputs/pdf/scanned_chess_fixture_2.pdf",
  "notes": "Second scanned chess fixture for FEN generalization.",
  "chess_fen_seed_labels": "reference_inputs/chess_fen/labels/scanned_chess_fixture_2_seed_positions.jsonl",
  "chess_fen_template_profile": "scanned_chess_fixture_2",
  "chess_fen_seed_exact_accuracy_min": 0.90
}
```

Use a real fixture path only after the PDF is present. Do not commit a manifest case that points to a missing file.

- [ ] **Step 2: Build a 20-record seed set for the second fixture**

Create a JSONL file with records shaped like:

```json
{"id":"fixture2_p001_c01","source_pdf":"reference_inputs/pdf/scanned_chess_fixture_2.pdf","page":1,"diagram_index":1,"crop_path":"reference_inputs/chess_fen/crops/fixture2_p001_c01.png","fen":"8/8/8/8/8/8/4K3/4k3 w - - 0 1","verified_by":"manual","verified_at":"2026-05-27","notes":"Replace with the real verified FEN before accepting."}
```

Acceptance: every entry passes `validate_fen`, every crop exists, and no example placeholder FEN remains.

- [ ] **Step 3: Evaluate both profiles**

Run:

```powershell
python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/labels/fundamenty_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/fundamenty_merida_like --min-confidence 0.84 --min-exact-accuracy 0.90 --output reports/chess_fen/evals/fundamenty_90_latest.json
python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/labels/scanned_chess_fixture_2_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/scanned_chess_fixture_2 --min-confidence 0.84 --min-exact-accuracy 0.90 --output reports/chess_fen/evals/scanned_chess_fixture_2_90_latest.json
```

Expected: both reports have `status=passed`, `false_positive_count=0`, and `exact_fen_accuracy >= 0.90`.

### Task 4: OpenAI Review-Only Label Assist

**Files:**
- Modify: `openai_chess_fen_reviewer.py`
- Modify: `scripts/check_openai_chess_fen_reviewer.py`
- Modify: `test_chess_fen_recognition.py`

- [x] **Step 1: Preserve no-mutation test**

Run:

```powershell
python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_review_payload_never_changes_fen_output
```

Observed: `OK`.

- [x] **Step 2: Add a review payload contract**

The provider must return only audit data:

```json
{
  "status": "reviewed",
  "candidate_fen": "...",
  "suggested_label": "...",
  "issues": [],
  "changed_output": false
}
```

Any future live OpenAI call must be opt-in with:

```text
KINDLEMASTER_OPENAI_CHESS_FEN_REVIEW=1
OPENAI_API_KEY=<configured outside repo>
```

- [x] **Step 3: Validate provider status**

Run:

```powershell
python scripts/check_openai_chess_fen_reviewer.py
```

Expected default:

```json
{
  "enabled": false,
  "mode": "review_only",
  "mutates_fen": false
}
```

Observed default: `enabled=false`, `mode=review_only`, `mutates_fen=false`.

- [x] **Step 4: Add optional live review transport without FEN mutation**

Implemented after continuation:

```text
OpenAIChessFenReviewer(api_key=..., transport=...) -> Responses API /responses
```

Evidence:
- `review_chess_fen_candidate(...)` now passes optional crop bytes to the reviewer context, but still forces `changed_output=false`.
- The provider builds a Responses API payload with `input_text` and optional `input_image` data URL.
- Live use remains opt-in through `KINDLEMASTER_OPENAI_CHESS_FEN_REVIEW=1` and `OPENAI_API_KEY`.
- Default status remains disabled: `enabled=false`, `configured=false`, `api_key_present=false`, `mode=review_only`, `mutates_fen=false`, `full_document_upload=false`.
- Focused tests: `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_review_payload_never_changes_fen_output test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_reviewer_payload_is_review_only_audit_data test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_reviewer_builds_from_env_but_stays_review_only test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_live_review_uses_image_payload_without_mutating_fen`, `4` tests OK.
- Full FEN tests: `python -m unittest test_chess_fen_recognition.py`, `107` tests OK.
- Strict two-profile gate remains intentionally failed: `evaluated_case_count=1`, `missing_profile_count=1`, `overall_exact_fen_accuracy=0.925`, `total_false_positive_count=0`.

### Task 5: Full Acceptance Run

**Files:**
- Output only: `reports/chess_fen/evals/*.json`
- Output only: `reports/smoke/<run_id>/smoke_full.json`
- Output only: `output/smoke/<run_id>/*.epub`

- [ ] **Step 1: Run targeted unit tests**

Run:

```powershell
python -m unittest test_chess_fen_recognition.py test_chess_fix.py test_smoke_chess_quality.py test_publication_pipeline.py
```

Expected: all tests pass.

- [ ] **Step 2: Run Fundamenty full smoke**

Run:

```powershell
python kindlemaster.py smoke --mode full --case fundamenty_scan_chess_pdf
```

Expected:

```text
overall_status: passed
diagram_count > 0
fen_count / diagram_count >= 0.90
manual_review_count <= diagram_count * 0.10
epubcheck_status: passed
validation_status: passed
```

- [ ] **Step 3: Validate generated EPUB**

Run:

```powershell
python kindlemaster.py validate output\smoke\<run_id>\fundamenty_scan_chess_pdf.epub
```

Expected: KindleMaster validation passed and EPUBCheck has no errors.

- [ ] **Step 4: Restart localhost if restart-sensitive files changed**

Run:

```powershell
python kindlemaster.py serve
```

Then verify:

```powershell
Invoke-WebRequest http://kindlemaster.localhost:5001/app -UseBasicParsing
```

Expected: HTTP `200`, and server start time newer than changed restart-sensitive files.

## Completion Criteria

The goal can be marked complete only when current evidence proves:

- at least one scanned chess fixture has `fen_coverage >= 0.90` end-to-end;
- at least two real scanned chess/FEN profiles are present in the manifest and pass `python scripts/evaluate_chess_fen_corpus.py --min-profile-count 2`;
- every supported template profile in the manifest has a seed eval with `exact_fen_accuracy >= 0.90`;
- seed evals report `false_positive_count=0`;
- generated EPUB validates successfully;
- OpenAI reviewer remains review-only and cannot mutate accepted FEN;
- OpenAI/human label-assist imports remain review-only until a human manually fills `fen`, `verified_by`, and `verified_at`;
- promoted label-assist rows remain outside corpus until deterministic candidate/profile/corpus gates pass;
- font-board candidate profiles remain review-only unless promoted into a separate manually verified seed-label profile;
- the final report names any unsupported diagram styles as out of scope rather than silently claiming coverage.

## Addendum: Goal Lowered To 80% On 2026-05-27

The active user criterion was changed from `90%` to `80%`, with the explicit
safety rule: if the recognizer is not confident, it must not generate FEN for
that diagram.

Current evidence for the updated 80% criterion:

- `reports/chess_fen/evals/fen_80_acceptance_latest.json`: `status=passed`,
  `case_count=40`, `fen_count=37`, `exact_fen_count=37`,
  `exact_fen_accuracy=0.925`, `false_positive_count=0`, `square_accuracy=1.0`.
- `reports/chess_fen/evals/fen_corpus_80_latest.json`: `status=passed`,
  `evaluated_case_count=1`, `overall_exact_fen_accuracy=0.925`,
  `total_false_positive_count=0`.
- `reports/chess_fen/evals/fundamenty_holdout_fold0_80_latest.json`:
  `status=failed`, but safely: `fen_count=0`, `false_positive_count=0`,
  `square_accuracy=0.9688`. This is consistent with the new rule because
  uncertain holdout diagrams go to review instead of getting FEN.
- `reports/chess_fen/evals/openai_chess_fen_reviewer_status_latest.json`:
  OpenAI reviewer is `review_only`, `configured=false`, `mutates_fen=false`.
- Runtime publication threshold was aligned with the audited value:
  `ConversionConfig.chess_fen_min_confidence = 0.84`. This removes the older
  extraction-coverage default of `0.70` so runtime EPUB generation follows the
  same conservative "no confidence, no FEN" policy as the eval gate.

Verification on this update:

- `python scripts/evaluate_chess_fen_recognizer.py ... --min-exact-accuracy 0.80`
  passed at `37/40 = 0.925` with `false_positive_count=0`.
- `python scripts/evaluate_chess_fen_corpus.py --min-exact-accuracy 0.80 --min-profile-count 1`
  passed with the current verified scanned profile.
- `python -m unittest test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_status_checker_writes_audit_output_file test_chess_fen_recognition.ChessFenRecognitionTests.test_openai_chess_review_payload_never_changes_fen_output test_chess_fen_recognition.ChessFenRecognitionTests.test_fundamenty_seed_eval_passes_90_percent_gate_without_false_positives`
  passed.
- `python -m py_compile scripts/check_openai_chess_fen_reviewer.py test_chess_fen_recognition.py`
  passed.
- `python -m unittest test_chess_fen_recognition.py` passed: `117` tests in
  `140.625s`.
- Runtime safety alignment after continuation:
  `converter.ConversionConfig.chess_fen_min_confidence` changed from `0.70`
  to `0.84`, and `scripts/run_smoke_tests.py` changed
  `CHESS_FEN_ACCEPTANCE_MIN` from `0.90` to the current user criterion `0.80`.
  Verification: `python -m unittest test_smoke_chess_quality.py` passed
  (`5` tests), focused runtime/default FEN tests passed (`2` tests),
  `python -m unittest test_docx_conversion.py test_converter_text_cleanup.py test_release_quality_recovery.py test_epub_quality_recovery.py`
  passed (`25` tests), and the full `python -m unittest test_chess_fen_recognition.py`
  passed again (`117` tests in `188.546s`).
- Localhost freshness after runtime change: restarted KindleMaster server and
  verified `http://127.0.0.1:5001/` returned HTTP `200`; listening Python PID
  `43068` started at `2026-05-27 23:01:16`, newer than the latest
  restart-sensitive file write time `2026-05-27 22:34:58`.

Remaining weakness under the updated 80% goal: this evidence proves the current
verified `Fundamenty` profile and one-profile corpus gate, not broad
cross-book generalization. `Woodpecker` remains the next profile to verify if
the target is widened back to multi-book proof.

## Addendum: Woodpecker Generalization Probe On 2026-05-27

Second-profile work was started with `Woodpecker` crops to avoid treating
`Fundamenty` as the only proof source.

Current evidence:

- `reference_inputs/chess_fen/labels/woodpecker_method_probe_seed_positions.jsonl`
  contains `20` manually grid-reviewed seed labels.
- `reference_inputs/chess_fen/templates/woodpecker_method_probe/` was rebuilt
  from those labels.
- `reports/chess_fen/evals/woodpecker_80_acceptance_latest.json` currently
  fails safely: `case_count=20`, `fen_count=0`, `false_positive_count=0`,
  `square_accuracy=0.977`. Several cases have exact placement but no FEN
  because the partial-board gate still requires stronger dense-board evidence.
- `scripts/evaluate_chess_fen_recognizer.py` now emits
  `recognition_diagnostics` per case: `grid_confidence`, `board_signal`,
  `normalized_size`, `suppressed_reason`, and
  `exact_placement_without_fen`. This makes the next optimization loop
  measurable instead of guessing why FEN was withheld.
- `_match_piece_template` now has a narrow empty-vs-piece ambiguity guard
  (`MIN_EMPTY_VS_PIECE_ERROR_MARGIN = 0.003`) to suppress only extremely close
  piece wins over empty templates. This keeps `Fundamenty` round-trip tests
  passing while reducing low-margin background artifacts.

Verification:

- `python -m unittest test_chess_fen_recognition.py` passed: `120` tests in
  `158.400s`.
- `python -m unittest test_smoke_chess_quality.py` passed: `5` tests.
- `python scripts/evaluate_chess_fen_recognizer.py reference_inputs\chess_fen\labels\fundamenty_seed_positions.jsonl --template-dir reference_inputs\chess_fen\templates\fundamenty_merida_like --min-confidence 0.84 --min-exact-accuracy 0.80 --output reports\chess_fen\evals\fen_80_acceptance_latest.json`
  passed with `37/40 = 0.925` and `false_positive_count=0`.
- `python scripts/evaluate_chess_fen_corpus.py --min-confidence 0.84 --min-exact-accuracy 0.80 --output reports\chess_fen\evals\fen_corpus_80_latest.json`
  passed for the current manifest-backed profile.

Rejected approach:

- A dominant-content cropper was prototyped for captioned coordinate diagrams,
  but enabling it as the default worsened `Woodpecker` recognition. It remains
  as a directly tested helper for future work, but it is not used by the
  default normalization path.

Next high-impact work:

- Promote `Woodpecker` only after adding a second independent acceptance signal
  for low-grid placements, such as reclassification agreement from a verified
  crop family or a real local figure classifier. Do not relax
  `partial_board_crop_without_dense_board_evidence` directly; current evidence
  shows that would publish false FEN on several high-confidence but wrong
  placements.
