# FX-011 Stage Artifact Manifest Pass

Date: 2026-04-13
Task: `FX-011`
Owner: `Lead / Orchestrator`
Supporting agents:
- `Linnaeus`: recommendation for persisted stage-artifact manifest design

## Goal

Reduce destructive-finalizer ambiguity by persisting stage-by-stage artifact evidence instead of only reporting aggregate proofs.

## What Changed

- `kindle_semantic_cleanup.py` now persists a manifest-backed artifact bundle for each finalizer stage:
  - `extract`
  - `css_normalization`
  - `semantic_recovery`
  - `navigation_rebuild`
  - `metadata_normalization`
  - `packaging`
- every stage now records:
  - `proof.json`
  - critical snapshot files such as `content.opf`, `title.xhtml`, `nav.xhtml`, `toc.ncx`, and packaged CSS where present
  - `sha256` for each persisted snapshot
  - stage-specific extra artifacts such as `toc_entries.json` and packaged EPUB snapshots
- `kindlemaster_end_to_end.py` now exposes:
  - `finalizer_artifact_root`
  - `finalizer_artifact_manifest_path`
  - `finalizer_artifact_manifest`

## Evidence

Persisted manifests now exist for the tracked guard corpus:

- `kindlemaster_runtime/output/reports/strefa-pmi-52-2026-finalizer-artifacts/manifest.json`
- `kindlemaster_runtime/output/reports/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411-finalizer-artifacts/manifest.json`
- `kindlemaster_runtime/output/reports/tactits-finalizer-artifacts/manifest.json`
- `kindlemaster_runtime/output/reports/cover-letter-iwo-2026-finalizer-artifacts/manifest.json`

Each manifest proves:

- stage sequence is explicit and ordered
- stage proofs are persisted separately from the final summary
- navigation survival can be traced to the exact post-navigation snapshot
- packaged output can be compared against the earlier stage snapshots without relying on transient temp directories

## Validation

- `python kindlemaster_end_to_end.py --pdf samples/pdf/strefa-pmi-52-2026.pdf --publication-id strefa-pmi-52-2026 --release-mode`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/9d0316f6261ee5f304dc285523800c9f646653af6ff7484fca73344495eaf411.pdf --publication-id newsweek-food-living-2026-01`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/tactits.pdf --publication-id chess-5334-problems-combinations-and-games --profile book`
- `python kindlemaster_end_to_end.py --pdf samples/pdf/cover-letter-iwo-2026.pdf --publication-id cover-letter-iwo-2026`
- `python -m pytest -q`

## Result

`FX-011` remains `IN_PROGRESS`.

This pass materially reduces diagnostic ambiguity and proves finalizer stage outputs with persistent artifacts, but the finalizer is still coordinated through one entrypoint and has not yet been decomposed into independently callable acceptance-bounded modules.
