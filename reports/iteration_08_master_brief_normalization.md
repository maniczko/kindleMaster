# Iteration 08 - Master Brief Normalization

## Iteration

- iteration_id: ITER-08
- phase: phase_minus_1_master_brief_normalization
- owner: Lead / Orchestrator
- date_range: 2026-04-11
- verdict: G-1 PASS

## State Transitions

- task: T-1-002
  from: DONE
  to: DONE
  gate: G-1
  result: PASS

## Completed

- task: T-1-002
  output: `project_control/backlog.yaml` remapped to the new PDF-and-EPUB master brief
  gate: G-1
- task: T-1-003
  output: AGENTS, orchestration, and control-plane docs aligned to the new role and phase model
  gate: G-1
- task: T0-001
  output: `project_control/input_registry.yaml` created with repo-local EPUB intake and explicit `EPUB_ONLY` start mode
  gate: G-1

## Review

- task: T1-001
  issue: none
  reason: PDF-only branch is preserved but deferred because the current repo contains no source PDFs

## Blocked

- task: none
  blocker: none
  linked_issue: none
  next_action: move to the first eligible post-normalization execution task

## Metrics Updated

- field: backlog.current_start_mode
  value: EPUB_ONLY

## Next Task

- task: T5-002
- owner: Text Cleanup
- reason_selected: first eligible TODO after backlog normalization and preserved completed work
