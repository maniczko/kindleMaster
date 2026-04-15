# Repository Boundary

## Purpose

This file defines the hard boundary between Kindle Master and any foreign or previously co-located product runtime.

- `KINDLE MASTER`: EPUB remediation workflow, control plane, localhost toolkit, and release gate
- foreign frontend/runtime: any external web application, shared build chain, or non-Kindle product assets

They must not be treated as one application.

## Kindle Master Scope

The following areas are in scope for current `kindle master` work:

- `project_control/`
- `reports/`
- `tests/`
- `.github/workflows/kindlemaster-python.yml`
- `VERSION`
- `samples/epub/`
- `samples/pdf/`
- `docs/` files that describe the EPUB remediation system
- `kindle_semantic_cleanup.py`
- `kindlemaster_manifest.py`
- `kindlemaster_pdf_analysis.py`
- `kindlemaster_pdf_to_epub.py`
- `kindlemaster_end_to_end.py`
- `kindlemaster_webapp.py`
- `kindlemaster_local_server.py`
- `kindlemaster_templates/`
- `kindlemaster_runtime/`
- `requirements-kindle-cleanup.txt`

## Protected Foreign Scope

The following areas belong to foreign product scope and must remain outside this repository:

- frontend runtime environment configuration
- Vite dev server behavior
- billing, auth, deck, and non-Kindle product logic
- Supabase schema and auth logic
- Node build assumptions not required by Kindle Master

## Testing Rule

Any foreign frontend is not a valid runtime acceptance test for `kindle master`.

Valid `kindle master` verification should focus on:

- EPUB input intake
- EPUB analysis artifacts
- cleanup and regression reports
- issue register and low-confidence queue
- release-readiness evidence
- Python remediation pipeline behavior

## Current Finding

The repository root now contains Kindle Master only.

Operational and physical isolation evidence for Kindle Master:

- dedicated local server: `kindlemaster_local_server.py`
- dedicated PDF-to-EPUB and end-to-end scripts: `kindlemaster_pdf_to_epub.py`, `kindlemaster_end_to_end.py`
- dedicated runtime and output root: `kindlemaster_runtime/`
- dedicated Python workflow: `.github/workflows/kindlemaster-python.yml`
- no root `src/`, `supabase/`, `index.html`, `package.json`, or frontend Vite entrypoint remain in this repository

Remaining isolation scope limits:

- this workspace cannot fully prove the internal state of any external repository unless it is audited separately
- release gating must therefore rely on Kindle Master-side isolation checks plus explicit documentation of reverse-side scope limits

## Operating Rule

From this point onward:

- do not recreate foreign product files inside this repository
- do not add shared root files that imply co-execution with any foreign frontend
- do not introduce shared output, cache, CI, env, or build assumptions
- keep any newly discovered cross-project contamination visible in `project_control/issue_register.yaml`
- keep release blocked if new direct coupling is found
