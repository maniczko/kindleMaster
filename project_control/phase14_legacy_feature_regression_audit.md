# Legacy Feature Regression Audit

Date: `2026-04-11`

## Goal

Verify whether the current Kindle Master repository still contains the previously available PDF toolkit features, with special attention to:

- A4 crop workflow
- per-page crop persistence
- crop preview
- crop-to-A4 export
- richer PDF analysis and conversion surface
- ownership of changes between the current repo and `C:\Users\user\Desktop\kindleMaster`

## Repositories Checked

- current working repo: `C:\Users\user\Desktop\quiz\quiz`
- external legacy Kindle Master folder: `C:\Users\user\Desktop\kindleMaster`

## Verdict

`FUNCTIONAL REGRESSION CONFIRMED`

The current repo contains a newer isolated Kindle Master runtime and end-to-end sample path, but it does not currently contain the fuller legacy PDF toolkit feature set that still exists in `C:\Users\user\Desktop\kindleMaster`.

## What Exists In The Current Repo

- repo-local samples in `samples/pdf/` and `samples/epub/`
- isolated runtime in `kindlemaster_runtime/`
- local server in `kindlemaster_local_server.py`
- simplified PDF analysis and PDF-to-EPUB scripts:
  - `kindlemaster_pdf_analysis.py`
  - `kindlemaster_pdf_to_epub.py`
  - `kindlemaster_end_to_end.py`
  - `kindle_semantic_cleanup.py`

## What Is Missing From The Current Repo

The current repo does not show the fuller PDF toolkit surface that exists in the external legacy folder, including:

- interactive A4 crop workflow
- per-page crop memory and reuse
- crop preview canvas and crop coverage messaging
- crop-to-A4 PDF export flow
- broader fixed-layout builder surface
- broader OCR module surface
- richer Flask-based PDF toolkit backend

## Evidence In The Legacy Folder

The external folder contains a materially richer Kindle Master codebase, including:

- `app.py`
- `converter.py`
- `fixed_layout_builder.py`
- `fixed_layout_builder_v2.py`
- `ocr_module.py`
- `publication_pipeline.py`
- `templates/index.html`

`templates/index.html` in the legacy folder explicitly contains:

- `Crop do A4`
- `crop box`
- `crop preview`
- `Pokaż preview`
- `Konwertuj do EPUB`
- `Analizuj`
- `Zaznacz obszar, ktory ma trafic na strone A4`
- client-side crop state and per-page crop persistence logic
- `Crop & Fit to A4`

## Evidence In The Current Repo

The current repo search found:

- preview support in `kindlemaster_local_server.py`
- basic PDF sample processing
- end-to-end PDF -> EPUB -> final EPUB support

But it did not find an equivalent A4 crop workflow or the broader toolkit surface from the legacy folder.

## File Count Signal

- current repo top-level Kindle Master Python scripts: `5`
- external legacy Kindle Master Python scripts: `35`
- common file names between them: only `kindle_semantic_cleanup.py`

This is strong evidence that the current repo is not carrying the full earlier Kindle Master implementation.

## Ownership / Location Finding

Not all Kindle Master changes are currently held in `C:\Users\user\Desktop\kindleMaster` only, and not all are held in the current repo only.

Actual state:

- the newer isolated runtime and control-plane work lives in the current repo
- the richer historical PDF toolkit still lives in `C:\Users\user\Desktop\kindleMaster`

So the implementation history is split across two locations.

## Risk

This split creates:

- feature regression risk
- source-of-truth ambiguity
- migration risk
- user confusion during localhost testing

## Required Remediation

- track regression as `ISSUE-017`
- prioritize `FX-007`
- decide which legacy mechanisms must be migrated into the current repo
- do not assume feature parity until the migrated workflow is re-tested on localhost
