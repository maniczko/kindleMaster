# Project Overview

`kindlemaster` is a generic autonomous publishing-remediation system operating toward one outcome:

`PDF -> baseline EPUB -> EPUB remediation -> final EPUB for premium Kindle reading`

Current mode:
- `PDF_AND_EPUB`

Current release state:
- `READY`

Current quality-first proof:
- active release publication passes single-publication release gate
- corpus-wide `quality-first` text-first gate passes on the current manifest-backed fixture bank
- no unjustified screenshot or page-image fallback remains on tracked normal text pages
- the repository remains in premium hardening mode even though current release truth is `READY`

Remaining hardening work:
- broaden fixture bank with OCR-stressed and stronger multi-page document-like samples
- widen release breadth beyond the single current release-eligible publication
- keep genericity guards and text-first proofs active as the corpus grows

Primary project commitments:
- no paraphrase
- no silent meaning drift
- no hidden overwrite of corrected outputs
- no unverified claims of improvement
- no final `READY` without premium audit and release gate evidence
- no release candidate without manifest-backed metadata and passing release gates
- no unresolved cross-project coupling with `foreign frontend/runtime`

Repository identity:
- this repository is the canonical `KINDLE MASTER` root
- extracted `foreign frontend/runtime` path: `an external repository path outside this workspace`
- reverse-side verification of the extracted `foreign frontend/runtime` repo remains scope-limited from this workspace, but Kindle Master-side isolation is still mandatory and release-relevant

Progress monitoring:
- `project_control/status_board.md`
- `project_control/backlog.yaml`
- `project_control/issue_register.yaml`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/metrics.json`
- `reports/`
