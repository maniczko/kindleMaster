# Exact-Crop Label Campaign: `Fundamenty 1-1`

Ten workflow domyka auto-akceptację przez `verified_exact_crop_label_used`, bez ruszania progów recognizera.

## Fala A: eksport i refresh stale hashy

```powershell
python scripts/export_verified_exact_crop_label_campaign.py reports/chess_fen/fundamenty_trim_test_rerun2.json --source-pdf "C:\Users\user\Downloads\Fundamenty 1-1.pdf" --output-dir reports/chess_fen/exact_label_campaign/fundamenty_rerun2
```

Sprawdź najpierw rekordy z `stale_exact_label=true` w:

- `reports/chess_fen/exact_label_campaign/fundamenty_rerun2/exact_label_draft.jsonl`
- `reports/chess_fen/exact_label_campaign/fundamenty_rerun2/review_sheet.html`

Po ręcznej weryfikacji wypełnij:

- `human_verified=true`
- `fen`
- `verified_by`
- `verified_at`

Następnie zaimportuj:

```powershell
python scripts/apply_verified_exact_crop_labels.py reports/chess_fen/exact_label_campaign/fundamenty_rerun2/exact_label_draft.jsonl --target-labels reference_inputs/chess_fen/labels/fundamenty_verified_crop_labels.jsonl --target-crops-dir reference_inputs/chess_fen/crops/imported_exact_review
```

## Fala B: rerun i kolejne exact labels

Po imporcie stale hashy uruchom pełny rerun:

```powershell
python kindlemaster.py convert "C:\Users\user\Downloads\Fundamenty 1-1.pdf" --output output/fundamenty_exact_label_campaign.epub --report-json reports/chess_fen/fundamenty_exact_label_campaign.json
```

Priorytet dalszej ręcznej pracy:

1. stale exact-label refresh
2. `threshold-only` / near-threshold
3. reszta `king_count_invalid` / `annotation_cross_marker_suppressed`

## Zasady bezpieczeństwa

- Exact labels są prawdą per `sha256` cropa, nie per `filename`.
- Stare wpisy o innym hashu zostają w `fundamenty_verified_crop_labels.jsonl`.
- Bez `human_verified=true` nic nie jest promowane do runtime exact labels.
