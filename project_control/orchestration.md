# Orchestration

## Main Sequence

1. Lead / Orchestrator
2. Cross-Project Boundary
3. Input & Intake
4. PDF Analysis
5. Baseline Conversion Strategy
6. EPUB Analysis
7. Text Cleanup
8. Structure & Semantics
9. TOC & Navigation
10. Image & Layout
11. CSS / Kindle UX
12. Quality Guardian / Release Gate
13. QA / Regression
14. Release Readiness

## Full Phase Model

1. Phase -2: cross-project boundary audit
2. Phase -1: repository and governance alignment
3. Phase 0: input intake and validation
4. Phase 1: PDF analysis and conversion strategy
5. Phase 2: baseline EPUB creation
6. Phase 3: EPUB inventory and baseline metrics
7. Phase 4: text anomaly detection
8. Phase 5: safe text cleanup
9. Phase 6: semantic reconstruction and segmentation
10. Phase 7: TOC and navigation cleanup
11. Phase 8: image/layout classification
12. Phase 9: Kindle CSS normalization
13. Phase 10: end-to-end validation
14. Phase 11: failed-improvement-cycle recovery audit
15. Phase 12: test suite and release gate hardening
16. Phase 13: QA, regression, premium audit, and release readiness

## Recovery Phases

Recovery phases are still valid and release-relevant:
- `R0` audit why improvement failed
- `R1` pipeline integrity and overwrite prevention
- `R2` semantic and navigation recovery
- `R3` text cleanup recovery
- `R4` front matter and special sections recovery
- `R5` Kindle premium tuning
- `R6` recovery verdict

## Task Picking Logic

1. Read backlog
1a. If `project_control/backlog_archive.yaml` exists, treat it as historical evidence, not as the active task queue
2. Read input registry
3. Read issue register
4. Build candidate set from `TODO`
5. Remove tasks whose dependencies are not `DONE`
6. Rank by priority `P0 > P1 > P2`
7. Break ties by lower phase, then lexical task id
8. Move chosen task to `IN_PROGRESS`
9. Execute
10. Update control files in mandatory order
11. Run assigned gate
12. Move to `DONE` only on evidence-backed pass
13. On fail, move to `REVIEW` or `BLOCKED`, update issues, create `FX-*` when required
14. If verdict is `NOT READY`, create continuation tasks and continue

## READY Maintenance Mode

After `READY`:
- `project_control/backlog.yaml` holds only active maintenance work
- `project_control/backlog_archive.yaml` preserves the full pre-release execution history
- any future quality-affecting change must:
  - re-enter the active backlog
  - rerun the hardened release gate
  - create a fresh immutable release candidate if release output changes

## Post-Task Update Order

1. `project_control/backlog.yaml`
2. `project_control/issue_register.yaml`
3. `project_control/metrics.json`
4. `project_control/low_confidence_review_queue.yaml`
5. `project_control/status_board.md`
6. current iteration report in `reports/`

## Authority Rules

- Lead / Orchestrator manages order but cannot override failed gates
- Quality Guardian / Release Gate owns release-blocking test enforcement
- QA / Regression owns measurable proof
- Release Readiness owns final READY / NOT READY

## Artifact Lifecycle

- PDF input enters through `samples/pdf/` or validated runtime upload paths
- baseline EPUB is written only to `kindlemaster_runtime/output/baseline_epub/`
- remediation writes final EPUB only to `kindlemaster_runtime/output/final_epub/`
- quality-loop candidates are written only to `kindlemaster_runtime/output/candidates/<iteration-id>/`
- reports live in `kindlemaster_runtime/output/reports/` and `reports/`
- final outputs must never be silently regenerated from earlier stages

## Quality Loop Overlay

- The quality loop builds an isolated candidate EPUB instead of overwriting the accepted final EPUB.
- Every candidate is compared against a dual baseline:
  - the currently accepted final EPUB
  - the legacy or best-known reference EPUB plus the PDF baseline EPUB
- Every candidate must pass:
  - release smoke
  - current Phase 12 pytest gates
  - finalizer proof gate
  - no new hard regressions on tracked guard publications
- A candidate is promoted only when it produces measurable improvement or closes a high-severity blocker without regressions.
- If there is no measurable improvement, the candidate is rejected and the accepted final EPUB remains unchanged.

## Stop Conditions

- missing required input
- unresolved isolation blocker
- critical editorial ambiguity needing human decision
- mandatory scenario coverage cannot be completed from available repo-local fixtures
- release verdict is READY

## Monitoring

- `project_control/status_board.md`
- `project_control/backlog.yaml`
- `project_control/issue_register.yaml`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/metrics.json`
- `reports/`
