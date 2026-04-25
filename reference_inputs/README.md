# KindleMaster Reference Inputs

This directory contains the curated input corpus used by the standard smoke workflow.

## Goals

- keep a small but representative set of publication classes,
- separate source fixtures from generated `output/` and `reports/`,
- give smoke tests stable inputs that are not tied to one publication only.

## How to populate

Run:

```powershell
python kindlemaster.py prepare-reference-inputs
```

This copies curated fixtures from `example/` and generates repo-local PDF and DOCX probes into:

- `reference_inputs/pdf/`
- `reference_inputs/epub/`
- `reference_inputs/docx/`

and writes `reference_inputs/manifest.json`.
The per-class size thresholds used by smoke and corpus sweeps live in `reference_inputs/size_budgets.json`.
Budget lookups normalize both underscored and hyphenated class labels, so `document_like_report` and `document-like-report` resolve to the same policy entry.

Manifest cases may set `release_strict: false` when they are validator or repair probes rather than release-ready publication candidates. In that case a passing source validation plus a failing release audit is reported as `passed_with_warnings`, not as a corpus blocker.

## Current reference classes

- `ocr_probe`
- `ocr_stress_scan`
- `dense_business_guide`
- `diagram_training_book`
- `magazine_layout`
- `document_like_report`
- `scan_probe`
- `docx_structured_report`
- `docx_rich_content`
- `docx_no_h1`

## Generated PDF fixtures

- `ocr_stress_scan` is a deterministic image-only scanned PDF used to keep OCR and scan detection honest.
- `document_like_report` is a generated multi-page report-style PDF used to keep document-like corpus coverage stronger than the tiny OCR probe.

## Standard smoke usage

Quick smoke:

```powershell
python kindlemaster.py smoke --mode quick
```

Full smoke:

```powershell
python kindlemaster.py smoke --mode full
```

## Rules

- Do not treat this corpus as exhaustive proof of correctness.
- Do not add publication-specific runtime logic because one fixture fails.
- Keep `release_strict` explicit for EPUB probes that are intentionally not release-ready.
- If a new failure mode appears repeatedly, add a representative fixture and update the smoke manifest.
- If a new document class is added to the manifest or corpus sweep, add its size thresholds to `reference_inputs/size_budgets.json` in the same change.
- Use `python scripts/benchmark_size_budgets.py` to generate candidate thresholds, but review and commit the JSON manually.
- Generated PDF fixtures should stay repo-local, and the OCR-stressed scanned PDF must remain deterministic.
