# FEN/PGN Agent Coordination Plan

Repozytorium: `maniczko/kindleMaster`  
Branch bazowy: `main`  
Dokument sterujacy: `docs/superpowers/plans/fen_pgn_codex_agent_execution_pack.md`  
Rola: Agent 00 - Orchestrator / Technical Lead

## Summary

Prace FEN/PGN dzielimy na 12 malych PR-ow. Celem pierwszej fali nie jest poprawianie recognizera w ciemno, tylko dodanie mierzalnego rozkladu pipeline'u, oddzielnych metryk FEN i PGN, datasetu diagnostycznego, audit harness oraz raportow top blockers. Kazdy PR ma osobne artefakty, testy, Definition of Done i zakazane dzialania.

## Kolejnosc i rownoleglosc

- Sekwencja krytyczna: `PR-01 -> PR-02 -> PR-03 -> PR-10 -> PR-11 -> PR-12`.
- FEN rownolegle po `PR-03`: `PR-04`, `PR-06`, `PR-07`; `PR-05` dopiero po `PR-04`.
- PGN rownolegle po `PR-03`: `PR-08 -> PR-09`.
- `PR-10` integruje wyniki `PR-03/04/06/08/09`.
- `PR-11` moze zaczac po `PR-02`, ale finalizuje po `PR-10`.
- `PR-12` jest wylacznie review/readiness i startuje po merge `PR-11`.

## PR-01 / Agent 01 - Baseline & Toolchain

- Branch: `codex/fen-pgn-01-baseline-toolchain`
- Cel: ustalic stan toolchainu OCR/FEN/PGN i dependency gaps.
- Zakres: raporty `doctor`, quick/corpus capability summary, decyzja `pytesseract` i `opencv-python-headless`.
- Pliki do zmiany: `requirements.txt`, `requirements-dev.txt`, `docs/toolchain-matrix.md`, ewentualnie doctor/capability reporting.
- Zakazane: recognizer, thresholds, acceptance gates, AI/model integration.
- Artefakty: `reports/audit/baseline_toolchain_report.json`, `reports/audit/dependency_gap_report.json`.
- Testy: `python kindlemaster.py doctor`, `python kindlemaster.py test --suite quick`; corpus jako smoke/degraded.
- Definition of Done: raport mowi `ok/degraded/unavailable` dla OCR/FEN/PGN; gaps maja next action; quick suite przechodzi albo ma jawny degraded reason.
- Zaleznosci: brak.

## PR-02 / Agent 02 - Dataset & Ground Truth

- Branch: `codex/fen-pgn-02-audit-dataset`
- Cel: dodac diagnostyczny dataset schema dla FEN/PGN/negative samples.
- Zakres: `reference_inputs/chess_fen/audit_2026_06/`, JSONL schemas, walidator datasetu.
- Pliki do zmiany: `scripts/validate_chess_audit_dataset.py`, `test_chess_audit_dataset.py`, katalogi datasetu z `.gitkeep`/manifestem.
- Zakazane: fake labels, tuning modeli, zmiana recognizera, zmiana gate'ow.
- Artefakty: `manifest.json`, `labels/fen_ground_truth.jsonl`, `labels/pgn_ground_truth.jsonl`, `labels/negative_samples.jsonl`.
- Testy: `python -m unittest test_chess_audit_dataset.py`, walidator na pustym/minimalnym fixture.
- Definition of Done: brak `human_verified`, brak cropa, brak placement albo bledny `diagram_only` sa raportowane deterministycznie; `diagram_only` jest poprawnym `pgn_feasible=false`.
- Zaleznosci: moze isc rownolegle z `PR-01`.

## PR-03 / Agent 03 - Pipeline Audit Harness

- Branch: `codex/fen-pgn-03-pipeline-audit-harness`
- Cel: dodac end-to-end pomiar etapow FEN i PGN bez naprawiania heurystyk.
- Zakres: `scripts/audit_chess_pipeline_breakdown.py`, oddzielny funnel FEN i PGN, top blockers.
- Pliki do zmiany: nowy audit script, `test_chess_pipeline_audit_harness.py`.
- Zakazane: acceptance changes, heuristic fixes, production data mutation, laczenie FEN/PGN w jednej metryce.
- Artefakty: `reports/chess_audit/<run_id>/audit_summary.json`, `audit_cases.jsonl`, `audit_cases.csv`, `top_fen_blockers.json`, `top_pgn_blockers.json`, `html/index.html`.
- Testy: unit fixture z jednym FEN, jednym PGN feasible, jednym `diagram_only`; smoke audit do `reports/chess_audit/test`.
- Definition of Done: kazdy case ma top blocker; FEN i PGN sa mierzone osobno; `diagram_only` nie obniza PGN rate.
- Zaleznosci: `PR-02`.

## PR-04 / Agent 04 - FEN Crop/Grid Diagnostics

- Branch: `codex/fen-pgn-04-crop-grid-diagnostics`
- Cel: udowodnic, czy FEN odpada na crop/grid/normalizacji.
- Zakres: diagnostyczny payload normalizacji, overlay 8x8, crop problem taxonomy.
- Pliki do zmiany: `chess_position_recognizer.py`, `scripts/audit_chess_pipeline_breakdown.py`, `test_chess_fen_crop_grid_diagnostics.py`.
- Zakazane: `machine_accept_fen`, thresholds, model changes, runtime auto-promotion.
- Artefakty: `reports/chess_audit/<run_id>/overlays/*.png`, crop taxonomy counts w audit summary.
- Testy: overlay generation na synthetic crop, taxonomy dla caption/coordinates/partial board.
- Definition of Done: kazdy FEN audit case moze wskazac crop taxonomy i overlay path; brak zmiany accepted count przez sam PR.
- Zaleznosci: `PR-03`.

## PR-05 / Agent 05 - OpenCV Geometry Spike

- Branch: `codex/fen-pgn-05-opencv-geometry-spike`
- Cel: sprawdzic diagnostycznie, czy OpenCV poprawia board quad/warp.
- Zakres: opcjonalny `chess_board_geometry_cv.py`, audit-only flag `KINDLEMASTER_CHESS_CV_GEOMETRY=1`, before/after metrics.
- Pliki do zmiany: `requirements-dev.txt`, `chess_board_geometry_cv.py`, audit harness, `test_chess_cv_board_geometry.py`.
- Zakazane: domyslne runtime uzycie CV, thresholds, acceptance changes.
- Artefakty: CV overlay images, before/after grid confidence and placement metrics.
- Testy: skip jesli OpenCV unavailable; synthetic rectangle/quad detection; audit smoke with flag off/on.
- Definition of Done: CV path jest optional i diagnostyczny; raport pokazuje czy pomaga; brak wplywu na default runtime.
- Zaleznosci: `PR-01`, `PR-04`.

## PR-06 / Agent 06 - FEN Semantics & Acceptance

- Branch: `codex/fen-pgn-06-placement-full-fen-semantics`
- Cel: rozdzielic placement FEN, full FEN i runtime accepted FEN w kodzie/raportach.
- Zakres: placement validation, status taxonomy, acceptance trace, blocker taxonomy.
- Pliki do zmiany: `chess_fen_hardening.py`, `chess_position_recognizer.py`, `chess_auto_flow.py`, `test_chess_fen_placement_vs_full_fen.py`.
- Zakazane: luzowanie `machine_accept_fen`, full FEN accepted przy inferred side-to-move, AI/external authority.
- Artefakty: audit fields `placement_valid`, `full_fen_valid`, `metadata_known`, `side_to_move_source`, `top_fen_blocker`.
- Testy: valid placement + unknown side => placement success, full FEN review; explicit side evidence can pass existing gates; false-positive fixtures remain blocked.
- Definition of Done: unknown side-to-move nie jest piece-recognition failure; full FEN acceptance pozostaje strict.
- Zaleznosci: `PR-03`.

## PR-07 / Agent 07 - Template Profile & Labels

- Branch: `codex/fen-pgn-07-profile-readiness`
- Cel: uporzadkowac readiness labels/templates dla diagnostic/runtime/corpus.
- Zakres: profile readiness breakdown, piece coverage, thresholds for diagnostic/runtime/corpus.
- Pliki do zmiany: `scripts/check_chess_fen_profile_ready.py`, `scripts/build_chess_piece_templates.py`, `chess_fen_ml_acceptance.py`, `test_chess_fen_profile_readiness.py`.
- Zakazane: fake labels, manifest promotion bez gates, AI/arbiter jako human evidence.
- Artefakty: `reports/chess_fen/profile_readiness_breakdown.json`.
- Testy: 0 labels, 20 labels diagnostic-only, 50 labels runtime-ready, incomplete piece coverage.
- Definition of Done: jeden status `profile_ready` nie ukrywa roznych poziomow readiness; operator dostaje next action.
- Zaleznosci: `PR-01`.

## PR-08 / Agent 08 - PGN Feasibility & OCR

- Branch: `codex/fen-pgn-08-pgn-feasibility`
- Cel: nie liczyc przypadkow bez movetext jako PGN failure.
- Zakres: `classify_pgn_feasibility`, fields `pgn_feasible`, `pgn_feasibility_reason`, `pgn_should_count_in_success_rate`, OCR diagnostics.
- Pliki do zmiany: `chess_pgn_extractor.py`, `chess_auto_flow.py`, ewentualnie capability-only `ocr_module.py`, `test_chess_pgn_feasibility.py`.
- Zakazane: generowanie PGN z `diagram_only`, acceptance threshold changes, replay bypass.
- Artefakty: PGN feasibility counts in audit summary and quality report.
- Testy: `diagram_only`, `insufficient_text`, `exercise_solution_line`, `full_game_text`, noisy OCR sample.
- Definition of Done: PGN success rate liczony tylko na feasible cases; infeasible cases maja reason, nie failure.
- Zaleznosci: `PR-03`.

## PR-09 / Agent 09 - PGN Replay & Export

- Branch: `codex/fen-pgn-09-pgn-replay-export-blockers`
- Cel: dodac stage metrics i blocker ranking dla strict PGN export.
- Zakres: parse/replay/final_fen/export breakdown, top blockers, strict export evidence.
- Pliki do zmiany: `chess_pgn_extractor.py`, `chess_pgn_auto_repair.py` tylko dla reporting, `chess_auto_flow.py`, `test_chess_pgn_replay_export.py`.
- Zakazane: export bez parser+python-chess replay, pgn-extract jako authority, review-only copyable strict PGN.
- Artefakty: `pgn_validation.json` stage breakdown, top blocker fields per rejected PGN.
- Testy: valid PGN exportable; illegal SAN review; unmapped glyph review; valid solution line from accepted FEN verified.
- Definition of Done: kazdy rejected feasible PGN ma `top_blocker`; exportable PGN ma parse/replay/final_fen proof.
- Zaleznosci: `PR-08`.

## PR-10 / Agent 10 - Dashboard & Reporting

- Branch: `codex/fen-pgn-10-readiness-dashboard`
- Cel: operator-facing raport prawdy o FEN/PGN automation.
- Zakres: quality report/html sections: FEN funnel, PGN feasible/infeasible split, top blockers, review queues, links.
- Pliki do zmiany: `chess_auto_flow.py`, `chess_study_export.py`, ewentualnie `static/js/quality-cockpit.js`, `test_chess_quality_report_fen_pgn_breakdown.py`.
- Zakazane: recognizer changes, gate changes, single blended FEN/PGN metric.
- Artefakty: report sections and links to JSONL/CSV/overlays/HTML audit.
- Testy: snapshot-like report test for FEN placement/full/runtime; PGN feasible/exportable; blocker links.
- Definition of Done: report odpowiada osobno: FEN placement automatic yes/no, full FEN automatic yes/no, PGN feasible automatic yes/no.
- Zaleznosci: `PR-03`, `PR-06`, `PR-08`, `PR-09`; benefits from `PR-04`.

## PR-11 / Agent 11 - QA / CI / Regression

- Branch: `codex/fen-pgn-11-qa-ci-regression`
- Cel: zablokowac regresje nowych metryk i optional toolchain behavior.
- Zakres: test aggregation, minimal fixtures, optional-tool skips, quick suite compatibility.
- Pliki do zmiany: nowe/istniejace testy `test_chess_*`, ewentualnie `kindlemaster.py` suite metadata tylko jesli konieczne.
- Zakazane: feature logic changes, dependency-heavy quick suite, optional tools required in CI.
- Artefakty: sample audit JSON snapshots, CI/quick suite evidence.
- Testy: `python kindlemaster.py test --suite quick`, targeted new tests; OpenCV/Tesseract skip/degraded assertions.
- Definition of Done: quick suite nie wymaga premium toolchain; nowe metrics maja tests; optional tools nie lamia CI.
- Zaleznosci: finalizuje po `PR-10`, moze zaczynac po `PR-02`.

## PR-12 / Agent 12 - Final Review / Readiness

- Branch: `codex/fen-pgn-12-final-readiness-review`
- Cel: ocenic, czy mozna uczciwie claimowac FEN/PGN automation.
- Zakres: final review report only; no feature implementation.
- Pliki do zmiany: ewentualnie docs/report generator integration, bez runtime changes.
- Zakazane: nowe heurystyki, threshold changes, post-facto metric massage, marketing success bez danych.
- Artefakty: final readiness report z FEN placement/full/runtime, PGN feasible/exportable, blockers, next 3 actions.
- Testy: `python kindlemaster.py doctor`, `python kindlemaster.py test --suite quick`, `python kindlemaster.py test --suite corpus`, audit harness on diagnostic dataset.
- Definition of Done: decyzja `merge / no merge / partial merge`; jesli DoD nie spelnione, raport mowi co blokuje, nie ze "rozpoznane".
- Zaleznosci: po merge `PR-11`.

## Prompt dla Agenta 01

```text
Pracujesz w repozytorium maniczko/kindleMaster na branchu bazowym main.

Rola:
Agent 01 - Baseline & Toolchain.

Cel:
Ustal, czy obecny poziom automatyzacji FEN/PGN jest ograniczony przez braki srodowiska lub zaleznosci. Nie implementuj recognizera, nie zmieniaj thresholdow i nie zmieniaj acceptance gate.

Kontekst:
Projekt rozdziela FEN i PGN na osobne pipeline'y. FEN/PGN automation moze byc raportowana tylko na podstawie mierzalnych etapow i deterministycznych proofow. AI, arbiter, high confidence ani external model nie sa samodzielnym zrodlem verified/corpus/runtime authority.

Zakres:
1. Sprawdz `requirements.txt` i `requirements-dev.txt`.
2. Sprawdz, czy `pytesseract` jest jawnie zadeklarowany, jesli `ocr_module.py` uzywa go jako fallback.
3. Sprawdz, czy OpenCV powinien byc wylacznie dependency dev/diagnostic dla przyszlych crop/grid diagnostics.
4. Uruchom lub przygotuj raporty dla:
   - `python kindlemaster.py doctor`
   - `python kindlemaster.py test --suite quick`
   - `python kindlemaster.py test --suite corpus` jako smoke/degraded, jesli pelny corpus jest zbyt ciezki.
5. Wygeneruj:
   - `reports/audit/baseline_toolchain_report.json`
   - `reports/audit/dependency_gap_report.json`

Dozwolone pliki:
- `requirements.txt`
- `requirements-dev.txt`
- `docs/toolchain-matrix.md`
- doctor/capability reporting tylko jesli obecny raport nie potrafi pokazac wymaganych stanow.

Zakazane dzialania:
- Nie zmieniaj `chess_position_recognizer.py`.
- Nie zmieniaj `machine_accept_fen`.
- Nie zmieniaj thresholdow.
- Nie dodawaj `easyocr`, `torch`, `tensorflow`, `scikit-image` ani nowego modelu.
- Nie mieszaj FEN i PGN w jednej metryce.

Artefakty:
- `reports/audit/baseline_toolchain_report.json`
- `reports/audit/dependency_gap_report.json`
- Krotki opis decyzji: `pytesseract` runtime/dev/none oraz `opencv-python-headless` dev/none.

Testy:
- `python kindlemaster.py doctor`
- `python kindlemaster.py test --suite quick`
- `python -m py_compile` dla zmienionych modulow/scripts, jesli dotyczy.

Definition of Done:
- Raport mowi jasno: OCR/FEN/PGN toolchain jest `ok`, `degraded` albo `unavailable`.
- Kazdy dependency gap ma `impact` i `next_action`.
- Quick suite przechodzi albo raport zawiera jawny degraded reason.
- Zaden runtime recognizer ani acceptance gate nie zostal zmieniony.
- Finalny raport uzywa formatu PLAN / IMPLEMENTATION / REVIEW.
```

## Merge checklist

- FEN i PGN sa raportowane osobno.
- `diagram_only` nie obniza PGN success rate.
- Thresholdy nie zostaly zmienione bez before/after i false-positive tests.
- AI/external/high-confidence nie tworzy verified/corpus/runtime authority.
- Kazdy PR ma artefakty, testy i Definition of Done.
- Finalny raport mowi, ktory poziom automatyzacji jest faktycznie osiagniety.
