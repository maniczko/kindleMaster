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

This copies curated fixtures from `example/` and generates deterministic DOCX probes into:

- `reference_inputs/pdf/`
- `reference_inputs/epub/`
- `reference_inputs/docx/`

and writes `reference_inputs/manifest.json`.

## Current reference classes

- `ocr_probe`
- `dense_business_guide`
- `diagram_training_book`
- `magazine_layout`
- `scan_probe`
- `docx_structured_report`
- `docx_rich_content`
- `docx_no_h1`

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
- If a new failure mode appears repeatedly, add a representative fixture and update the smoke manifest.
