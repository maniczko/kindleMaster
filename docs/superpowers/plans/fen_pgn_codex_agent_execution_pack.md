# FEN/PGN Codex Agent Execution Pack - KindleMaster

Repozytorium: `maniczko/kindleMaster`  
Branch bazowy: `main`  
Rola dokumentu: dokument sterujacy dla pracy Agentow 00-12 nad automatyzacja FEN/PGN.

## Cel

Doprowadzic pipeline do stanu, w ktorym mozna uczciwie powiedziec:

- FEN jest automatycznie rozczytany dla rozpoznawalnych diagramow na poziomie jawnie zmierzonych etapow.
- PGN jest automatycznie rozczytany tylko dla przypadkow, ktore realnie zawieraja dane PGN.

Nie wolno claimowac pelnej automatyzacji, jezeli raport pokazuje tylko czesciowy sukces, np. poprawny crop bez poprawnego placement albo diagram-only bez movetext.

## Najwazniejsza decyzja techniczna

Nie zaczynamy od nowego modelu, promptu AI, OCR magic ani zmiany thresholdow. Najpierw mierzymy pipeline etapami:

```text
PDF/page
-> diagram detection
-> crop
-> board normalization
-> grid 8x8
-> square classification
-> FEN placement
-> full FEN
-> machine acceptance
-> PGN feasibility
-> OCR/SAN extraction
-> PGN parse
-> python-chess replay
-> final_fen
-> exportable PGN
```

## Definicje

- `FEN placement`: pierwsze pole FEN, czyli sama pozycja figur.
- `Full FEN`: szesciopolowy FEN z active color, castling, en-passant, halfmove i fullmove.
- `Runtime accepted FEN`: FEN, ktory przeszedl deterministyczny gate i moze byc uzyty automatycznie.
- `PGN feasible`: przypadek z realnym movetextem, sekwencja ruchow, rozwiazaniem cwiczenia albo pelna partia.
- `PGN infeasible`: sam diagram, diagram z podpisem typu `White to move`, albo brak historii ruchow.

`diagram_only` nigdy nie moze obnizac PGN success rate.

## Globalne reguly agentow

1. Nie mieszac FEN i PGN w jednej metryce.
2. Nie liczyc `diagram_only` jako PGN failure.
3. Nie zmieniac thresholdow bez raportu before/after.
4. Nie luzowac `machine_accept_fen()` bez testu false-positive.
5. Nie promowac AI/external/high-confidence candidate bez deterministycznego proofu.
6. Nie traktowac manual review jako automatyzacji.
7. Nie dodawac nowego modelu bez datasetu, confusion matrix i top blockers.
8. Kazdy PR musi miec artefakty, testy i Definition of Done.
9. Jezeli agent czegos nie potwierdzil w kodzie lub danych, ma napisac: `Nie znalazlem dowodu w repo`.

## Finalna Definition of Done

### FEN

```text
diagram_detection_rate >= 95%
crop_correct_rate >= 95%
grid_correct_rate >= 95%
placement_fen_exact_rate >= 90%
false_machine_accept_count = 0
top blockers known for all rejected cases
```

### PGN

```text
pgn_feasibility_classification_rate = 100%
diagram_only not counted as PGN failure
pgn_exportable_rate measured only on feasible cases
all exportable PGNs pass parser + python-chess replay + final_fen check
top blockers known for all rejected feasible cases
```

### Raporty

```text
reports/chess_audit/latest/audit_summary.json
reports/chess_audit/latest/audit_cases.jsonl
reports/chess_audit/latest/audit_cases.csv
reports/chess_audit/latest/top_fen_blockers.json
reports/chess_audit/latest/top_pgn_blockers.json
reports/chess_audit/latest/html/index.html
```

Jezeli ktorykolwiek punkt nie jest spelniony, finalny komunikat ma brzmiec:

```text
Mamy automatycznie rozpoznany <konkretny poziom>, ale blokuje nas <konkretny blocker>.
```

## Podzial agentow

| Agent | Rola | Glowne wyjscie | Czego nie robi |
|---|---|---|---|
| Agent 00 | Orchestrator / Technical Lead | plan PR, merge order, dependencies | nie implementuje CV/OCR |
| Agent 01 | Baseline & Toolchain | raport srodowiska i zaleznosci | nie zmienia recognizera |
| Agent 02 | Dataset & Ground Truth | dataset schema + validator | nie stroi modeli |
| Agent 03 | Pipeline Audit Harness | end-to-end audit JSON/CSV/HTML | nie naprawia heurystyk |
| Agent 04 | FEN Crop/Grid Diagnostics | overlay 8x8 + crop taxonomy | nie zmienia acceptance gate |
| Agent 05 | OpenCV Geometry Spike | optional CV detector/warper | nie wlacza CV do runtime bez dowodu |
| Agent 06 | FEN Semantics & Acceptance | placement vs full FEN vs runtime | nie luzuje gate bez testow |
| Agent 07 | Template Profile & Labels | profile readiness i label flow | nie tworzy fake labels |
| Agent 08 | PGN Feasibility & OCR | pgn_feasible gate i OCR blockers | nie generuje PGN z diagram-only |
| Agent 09 | PGN Replay & Export | parse/replay/final_fen/export | nie akceptuje PGN bez replay |
| Agent 10 | Dashboard & Reporting | FEN/PGN funnel + blockers | nie zmienia recognizera |
| Agent 11 | QA / CI / Regression | testy regresji | nie zmienia feature logic |
| Agent 12 | Final Review Agent | final readiness report | nie implementuje nowych zmian |

## Kolejnosc PR-ow

1. PR-01: baseline + dependency report.
2. PR-02: diagnostic dataset schema + validator.
3. PR-03: audit harness end-to-end.
4. PR-04: FEN crop/grid diagnostics + overlays.
5. PR-05: optional OpenCV geometry spike.
6. PR-06: FEN placement vs full FEN semantics.
7. PR-07: template profile readiness unification.
8. PR-08: PGN feasibility gate.
9. PR-09: PGN replay/export blocker reporting.
10. PR-10: dashboard/report integration.
11. PR-11: QA/CI regression gates.
12. PR-12: final review report.

## Minimalne zaleznosci do rozwazenia

Runtime albo optional runtime:

```text
pytesseract>=0.3.13
```

Dev/diagnostic:

```text
opencv-python-headless>=4.10.0
```

Nie dodawac teraz:

```text
easyocr
torch
tensorflow
scikit-image
rapidfuzz
```

## Komendy referencyjne

```bash
python kindlemaster.py doctor
python kindlemaster.py test --suite quick
python kindlemaster.py test --suite corpus
python scripts/validate_chess_audit_dataset.py reference_inputs/chess_fen/audit_2026_06/manifest.json
python scripts/audit_chess_pipeline_breakdown.py reference_inputs/chess_fen/audit_2026_06/manifest.json --output reports/chess_audit/latest
python scripts/evaluate_chess_fen_recognizer.py reference_inputs/chess_fen/audit_2026_06/labels/fen_ground_truth.jsonl --template-dir reference_inputs/chess_fen/templates/audit --output reports/chess_fen/evals/audit.json
```

## Finalny raport oczekiwany po pracach

```text
# FEN/PGN Automatic Readiness Report

## Executive summary
- FEN placement automatic: yes/no
- Full FEN automatic: yes/no
- PGN automatic for feasible cases: yes/no

## Dataset
- FEN cases
- PGN feasible cases
- PGN infeasible cases
- negative samples

## FEN funnel
- diagram detected
- crop correct
- grid correct
- placement exact
- full FEN valid
- runtime accepted
- false accepted

## PGN funnel
- feasible
- OCR text present
- candidate blocks
- SAN tokens
- parse clean
- replay legal
- final FEN
- exportable

## Top blockers
- FEN blockers
- PGN blockers

## Decision
- merge / no merge / partial merge
- next 3 actions
```

## Czego nie robic dalej

- Nie dodawac kolejnego modelu bez datasetu i confusion matrix.
- Nie poprawiac PGN dla diagram-only.
- Nie luzowac `machine_accept_fen()` bez testu false positives.
- Nie traktowac pelnego FEN jako znanego, jezeli side/castling/en-passant sa inferowane.
- Nie uznawac manual review za automatyczny sukces.
- Nie mieszac OCR PGN z diagramowym FEN.
- Nie mowic `mamy rozczytane PGN`, jezeli feasible cases nie przechodza parse/replay/export.
- Nie mowic `mamy rozczytane FEN`, jezeli poprawny jest tylko crop, ale nie placement.
