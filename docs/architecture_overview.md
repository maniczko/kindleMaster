# Architecture Overview

The repository is organized into:
- a control plane
- an execution pipeline
- a release gate layer

## Control Plane

Authoritative files:
- `AGENTS.md`
- `.codex/config.toml`
- `project_control/backlog.yaml`
- `project_control/input_registry.yaml`
- `project_control/issue_register.yaml`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/metrics.json`
- `project_control/status_board.md`
- `reports/`

## Execution Pipeline

1. cross-project boundary audit
2. governance alignment
3. intake and execution-mode detection
4. PDF analysis and conversion strategy
5. baseline EPUB creation or EPUB intake
6. EPUB inventory and baseline metrics
7. text anomaly detection
8. safe text cleanup
9. semantic reconstruction and segmentation
10. TOC and navigation cleanup
11. image/layout handling
12. Kindle CSS / UX normalization
13. end-to-end validation
14. failed-improvement recovery
15. test suite and release gate hardening
16. premium audit and release readiness

## Release Gate Layer

Release is blocked unless:
- isolation passes
- required scenario coverage exists
- required tests exist and pass
- regression proof exists
- premium audit passes
- final artifact is measurably better than baseline

## Design Constraints

- generic across document-like, book-like, report-like, magazine-like, and mixed-layout inputs
- deterministic cleanup first
- AI-assisted logic only after controls exist
- no silent closure of ambiguity
- no destructive overwrite of final outputs
- no shared runtime, build, output, cache, or CI assumptions with `foreign frontend/runtime`
