# Phase 1 PDF Risk Report

Date: `2026-04-11`

Source inventory: `project_control/phase1_pdf_inventory.json`

## Summary

- `strefa-pmi-52-2026.pdf`: text-heavy, lowest immediate conversion risk
- `9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf`: text-heavy with some likely non-text pages and long-publication navigation pressure
- `tactits.pdf`: long publication with high page count and likely navigation pressure, even though text extraction is present

## Cross-cutting Risks

- long-publication navigation noise
- possible non-text pages inside otherwise text-heavy PDFs
- mixed-layout and figure-density continuation risk for later remediation phases

## Phase 1 Verdict

`G1` passes for the current sample corpus.

The PDF-first path is now analyzable and no longer blocked on missing input.
