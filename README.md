# Kindle Master

`Kindle Master` is the canonical repository for a governed remediation pipeline:

`PDF -> baseline EPUB -> EPUB remediation -> final EPUB for premium Kindle reading`

## Mission

This repository exists to:
- detect repo-local PDF and/or EPUB inputs
- create or register a baseline EPUB
- remediate structure, text, navigation, metadata, and Kindle UX
- enforce release-blocking quality gates
- continue iterating until the output is either `READY` or blocked by a real logged human dependency

## Runtime

Primary entrypoints:
- `kindlemaster_end_to_end.py`
- `kindlemaster_webapp.py`
- `kindlemaster_local_server.py`
- `kindlemaster_pdf_to_epub.py`
- `kindlemaster_pdf_analysis.py`
- `kindle_semantic_cleanup.py`

Local tester:
- `start-kindlemaster-local.ps1`
- `start-kindlemaster-local.bat`

End-to-end runner:
- `run-kindlemaster-e2e.ps1`
- `run-kindlemaster-e2e.bat`

## Control Plane

Authoritative project control lives in:
- `AGENTS.md`
- `project_control/backlog.yaml`
- `project_control/issue_register.yaml`
- `project_control/metrics.json`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/status_board.md`
- `project_control/publication_manifest.yaml`
- `reports/`

## Release Rules

The project is not releasable unless all are true:
- required tests exist
- required tests pass
- premium Kindle audit passes
- final score meets threshold
- final artifact is distinct from baseline
- no unresolved high or critical release blockers remain
- no unresolved foreign runtime or frontend coupling remains
- immutable release candidate evidence exists

## Isolation

This repository is Kindle Master only.

Any future reintroduction of shared runtime, build, CI, output, cache, workspace, or env assumptions is a release blocker.
