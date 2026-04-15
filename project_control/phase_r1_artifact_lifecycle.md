# Phase R1-001: Artifact Lifecycle Specification

Date: 2026-04-12
Owner: Lead / Orchestrator
Gate: `GR1`

## Purpose

Make the baseline, working, and final EPUB lifecycle explicit so later stages cannot silently overwrite or invalidate earlier improvements.

## Canonical Artifact Chain

1. `samples/pdf/*.pdf`
   Source input for the end-to-end path.

2. `kindlemaster_runtime/output/baseline_epub/*.epub`
   Baseline conversion output.
   This is the immutable input to remediation for a given run.

3. Temporary extracted EPUB working tree inside `finalize_epub_for_kindle()`
   This is the only place where XHTML, nav, CSS, and metadata are rewritten during remediation.
   It is treated as an intermediate working artifact and is never a release output.

4. `kindlemaster_runtime/output/final_epub/*.epub`
   Final remediated artifact.
   Only this path is allowed to represent the post-remediation result.

5. `kindlemaster_runtime/output/reports/*-end-to-end.json`
   Proof artifact for the run.
   It must include baseline validation and final validation.

## Non-Overwrite Rules

- Baseline conversion may write only to `baseline_epub/`.
- Remediation may read baseline bytes, but it may not write back into `baseline_epub/`.
- The finalizer may mutate only its temporary extracted working tree.
- The packed remediation result may write only to `final_epub/`.
- Validation reports may not be treated as the final artifact; they are evidence only.

## Final Proof Gate

The final artifact is not accepted unless the report confirms:

- `nav.xhtml` exists
- `toc.ncx` exists
- stylesheet references resolve to packaged files
- navigation targets resolve to packaged files and anchors
- `title.xhtml` exists and is not empty

## Active Recovery Mapping

- `ISSUE-018` / `FX-008`: final navigation paths
- `ISSUE-019` / `FX-009`: final stylesheet lifecycle
- `ISSUE-020` / `FX-010`: heading promotion and title/front matter semantics
- `ISSUE-022` / `FX-011`: destructive finalizer design and proof gate

## Current Interpretation

Phase R1 is complete only when the lifecycle is documented, the workflow forbids overwrite-prone stages, and the runner proves that the final artifact actually carries valid navigation, CSS, and title-page outputs.
