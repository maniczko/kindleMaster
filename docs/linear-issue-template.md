# KindleMaster Linear Issue Template

Related Linear scope: VAT-174.

Copy this template into Linear for conversion-quality work. Keep it KindleMaster-specific: every issue should explain how it affects PDF/DOCX -> EPUB quality, Kindle reading experience, runtime reliability, or release confidence.

## Template

```markdown
## Context

What user-visible or release-confidence problem does this address?

## Problem

What is broken, incomplete, risky, or unclear today?

## Root cause

Known:
- ...

Unknown / to verify:
- ...

## Expected effect

After this work, KindleMaster should:
- ...

## Affected conversion-quality area

Choose one or more:
- input routing / upload / async conversion
- PDF extraction / OCR
- DOCX extraction
- publication analysis / route selection
- text cleanup / PL-EN normalization
- heading / TOC / anchors
- references / bibliography / links
- metadata / language / description
- tables / lists / images / diagrams
- EPUB package / nav / spine / validation
- quality report / release checklist
- corpus / fixture breadth
- CI / governance / docs

## Scope

In:
- ...

Out:
- ...

## Acceptance criteria

- ...

## Suggested tests

Required:
- `python kindlemaster.py test --suite quick`

Add when relevant:
- `python kindlemaster.py test --suite corpus`
- `python kindlemaster.py test --suite release`
- `python kindlemaster.py test --suite browser`
- `python kindlemaster.py test --suite runtime`
- `python kindlemaster.py audit path\to\artifact.epub`
- `python kindlemaster.py workflow baseline path\to\input.pdf --change-area <area>`
- `python kindlemaster.py workflow verify path\to\input.pdf --run-id <run_id>`

## Risks

- ...

## Final report requirements

Agent must report:
- changed files,
- commands run with pass/fail,
- generated evidence paths,
- release checklist verdict if conversion output changed,
- residual blockers,
- whether localhost was restarted when restart-sensitive files changed.
```

## Priority Guide

| Priority | Use when |
| --- | --- |
| P0 | App cannot convert, output is unusable, data is lost, or a release blocker is active. |
| P1 | Major EPUB validity, nav/spine, metadata, heading, reference, or corpus blocker. |
| P2 | Important quality or maintainability work that improves generalization or reporting. |
| P3 | Documentation, polish, or non-blocking governance cleanup. |

## Short Examples

### CI / governance

- Problem: READY gates do not publish enough evidence for release handoff.
- Acceptance: CI exposes stable check names and artifacts; local command mapping is documented.
- Suggested tests: `python -m unittest test_github_ready_enforcement.py test_project_status.py`.

### EPUB validation

- Problem: final EPUB passes smoke but fails internal href validation.
- Acceptance: broken href count is zero or explicitly blocked with report evidence.
- Suggested tests: `python kindlemaster.py validate path\to\file.epub`, targeted validation unit tests, `python kindlemaster.py audit path\to\file.epub`.

### OCR cleanup

- Problem: OCR output leaves visible split/glued text in final chapters.
- Acceptance: cleanup improves affected fixture without damaging URLs, anchors, IDs, or code-like fragments.
- Suggested tests: `python -m unittest test_text_normalization.py test_converter_text_cleanup.py test_semantic_epub_cleanup.py`.

### TOC generation

- Problem: TOC is shallow or points at invalid anchors.
- Acceptance: nav entries are meaningful, anchors resolve, and ambiguous headings are review-flagged.
- Suggested tests: `python -m unittest test_epub_heading_repair.py test_toc_segmentation.py`.

### UI quality reporting

- Problem: `/convert/quality/<job_id>` hides fallback/manual-review signals from the operator.
- Acceptance: quality state is additively exposed and existing async download contract remains intact.
- Suggested tests: `python -m unittest test_app_async_convert.py test_app_quality_state_route.py test_quality_state_service.py`.
