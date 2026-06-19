# FEN/PGN Codex Agent Execution Pack

Repo: `maniczko/kindleMaster`

Cel: przejść z losowych poprawek do mierzalnej automatyzacji FEN/PGN. Ten plik jest repozytoryjnym indeksem planu dla Codex/agentów. Pełny pakiet promptów agentowych został przygotowany jako osobny artefakt Markdown.

## Zasady

1. Nie mieszać FEN i PGN w jednej metryce.
2. Nie liczyć `diagram_only` jako PGN failure.
3. Nie luzować `machine_accept_fen()` bez testów false-positive.
4. Nie promować AI candidate bez deterministycznego proofu.
5. Każdy PR musi mieć testy, artefakty i metryki.

## Podział agentów

| Agent | Rola | Wynik |
|---|---|---|
| 00 | Orchestrator / Technical Lead | plan PR i merge checklist |
| 01 | Baseline & Toolchain | raport środowiska i zależności |
| 02 | Dataset & Ground Truth | dataset diagnostyczny + walidator |
| 03 | Pipeline Audit Harness | end-to-end audit JSON/CSV/HTML |
| 04 | FEN Crop/Grid Diagnostics | overlay 8x8 i crop taxonomy |
| 05 | OpenCV Geometry Spike | opcjonalny CV detector/warper |
| 06 | FEN Semantics & Acceptance | placement vs full FEN vs runtime accepted |
| 07 | Template Profile & Labels | profile readiness i label flow |
| 08 | PGN Feasibility & OCR | pgn_feasible gate i OCR blockers |
| 09 | PGN Replay & Export | parse/replay/final_fen/export metrics |
| 10 | Dashboard & Reporting | FEN/PGN funnel + top blockers |
| 11 | QA / CI / Regression | testy regresji |
| 12 | Final Review Agent | final readiness report |

## Kolejność PR

1. Baseline + dependency report.
2. Diagnostic dataset schema + validator.
3. Audit harness end-to-end.
4. FEN crop/grid diagnostics + overlays.
5. Optional OpenCV geometry spike.
6. FEN placement vs full FEN semantics.
7. Template profile readiness unification.
8. PGN feasibility gate.
9. PGN replay/export blocker reporting.
10. Dashboard/report integration.
11. QA/CI regression gates.
12. Final review report.

## Definition of Done

FEN:

```text
diagram_detection_rate >= 95%
crop_correct_rate >= 95%
grid_correct_rate >= 95%
placement_fen_exact_rate >= 90%
false_machine_accept_count = 0
top blockers known for all rejected cases
```

PGN:

```text
pgn_feasibility_classification_rate = 100%
diagram_only not counted as PGN failure
pgn_exportable_rate measured only on feasible cases
all exportable PGNs pass parser + python-chess replay + final_fen check
top blockers known for all rejected feasible cases
```

## Minimalne nowe zależności do rozważenia

- `pytesseract>=0.3.13` — runtime albo optional runtime, jeśli direct OCR fallback ma działać lokalnie.
- `opencv-python-headless>=4.10.0` — dev/diagnostic dependency dla crop/grid/geometry spike.

Nie dodawać teraz: `easyocr`, `torch`, `tensorflow`, `scikit-image`, `rapidfuzz` bez dowodu z top blockers.

## Najważniejsze artefakty docelowe

```text
reports/chess_audit/latest/audit_summary.json
reports/chess_audit/latest/audit_cases.jsonl
reports/chess_audit/latest/audit_cases.csv
reports/chess_audit/latest/top_fen_blockers.json
reports/chess_audit/latest/top_pgn_blockers.json
reports/chess_audit/latest/html/index.html
```

## Finalny komunikat sukcesu

Nie wolno pisać `mamy rozczytane FEN/PGN`, jeśli metryki tego nie potwierdzają. W takim przypadku finalny komunikat ma brzmieć:

```text
Mamy automatycznie rozpoznany <konkretny poziom>, ale blokuje nas <konkretny blocker>.
```
