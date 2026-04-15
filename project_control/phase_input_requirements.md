# Phase Input Requirements

## Phase 0

Minimum repo-local assets:
- at least one EPUB or unpacked EPUB path for EPUB-first intake
- at least one PDF path for the full end-to-end PDF-first path

If the PDF asset is missing:
- execution mode may remain `EPUB_ONLY`
- end-to-end status must become `BLOCKED_NO_PDF`
- `ISSUE-015` and `FX-005` must stay visible

## Phase 1

PDF analysis requires:
- at least one discoverable repo-local PDF
- path registration in `project_control/input_registry.yaml`

If no PDF exists:
- `T1-001` must not be marked `DONE`
- the task should move to `BLOCKED`
- the blocker must remain linked to `ISSUE-015`

## Phase 2

Baseline conversion requires:
- a repo-local PDF fixture
- a validated Kindle Master-specific PDF-to-EPUB toolchain
- an output path that does not reuse foreign frontend/runtime runtime or build folders

If the toolchain is missing:
- `ISSUE-016` and `FX-006` stay open
- end-to-end validation cannot proceed

## Phase 3 and later

EPUB remediation phases require:
- a registered baseline EPUB artifact for the active path
- issue register, metrics, and status board present
- low-confidence review queue available for uncertain cases

Historical EPUB-only evidence can remain visible for comparison, but it does not satisfy end-to-end readiness by itself.
