# Iteration 23: Document Fixture And Atomic Outputs

Date: 2026-04-12

## Summary

This iteration closed the missing `document_like` scenario-coverage blocker with a real repo-local fixture and hardened EPUB artifact writes so overlapping local verification no longer risks exposing partial zip files.

## Changes

- added `samples/pdf/cover-letter-iwo-2026.pdf` as a real `document_like` fixture
- registered `cover-letter-iwo-2026` in `project_control/publication_manifest.yaml`
- updated scenario coverage tests so `document_like` is exercised by a real fixture instead of an explicit blocker
- generated:
  - `kindlemaster_runtime/output/baseline_epub/cover-letter-iwo-2026.epub`
  - `kindlemaster_runtime/output/final_epub/cover-letter-iwo-2026.epub`
- changed baseline and final EPUB writes to atomic replacement in:
  - `kindlemaster_pdf_to_epub.py`
  - `kindlemaster_end_to_end.py`
- corrected CLI language handling so manifest-backed language metadata is not silently overridden by a default `pl`
- refreshed smoke, release-gate, and quality-loop evidence on the active sample

## Evidence

### Active Release Sample

- source: `samples/pdf/strefa-pmi-52-2026.pdf`
- final EPUB: `kindlemaster_runtime/output/final_epub/strefa-pmi-52-2026.epub`
- smoke: `PASS`
- quality score: `10.0 / 10`
- pytest: `29 passed`
- release gate: `FAIL` only because open high-severity issues remain

### New Document-Like Fixture

- source: `samples/pdf/cover-letter-iwo-2026.pdf`
- final EPUB: `kindlemaster_runtime/output/final_epub/cover-letter-iwo-2026.epub`
- weighted score: `9.7 / 10`
- split words: `0`
- joined words: `0`
- boundary count: `0`
- manifest status: `real_repo_local`, `release_eligible=false`

## Resolved Blockers

- `ISSUE-001` resolved
- `ISSUE-025` resolved
- `ISSUE-027` resolved
- `ISSUE-028` resolved
- `FX-014` done

## Remaining Release Blockers

- `ISSUE-004` mixed-layout image-heavy handling risk
- `ISSUE-020` mixed-layout opening recovery still incomplete
- `ISSUE-022` finalizer still too monolithic
- `ISSUE-023` page-split continuity still incomplete
- `ISSUE-012` Phase 13 release evidence still incomplete

## Next Task

`FX-012` - Add continuation-aware article-opening recovery for page-split magazine XHTML
