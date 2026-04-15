# Codex Multi-Agent Orchestration Setup

This file is the persisted operational source of truth for the EPUB post-processing project.

## Primary Rule

Do not start implementing the EPUB remediation engine immediately.
First:

1. prepare the project structure
2. move tasks into backlog and control files
3. define agent roles
4. set quality gates
5. set the issue register
6. set the status board
7. run a dry simulation

Only after setup is evaluated as `READY FOR EXECUTION` may real implementation begin.

## Mandatory Control Files

- `project_control/backlog.yaml`
- `project_control/issue_register.yaml`
- `project_control/metrics.json`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/status_board.md`

## Agent Roles

- Lead / Orchestrator Agent
- EPUB Analysis Agent
- Text Cleanup Agent
- Structure & Semantics Agent
- TOC & Navigation Agent
- Image & Layout Agent
- CSS / Kindle UX Agent
- QA / Regression Agent

## Global Loop

1. read `project_control/backlog.yaml`
2. choose the highest-priority `TODO` task whose dependencies are `DONE`
3. set `IN_PROGRESS`
4. execute only within the task scope
5. update backlog, issue register, metrics, low-confidence queue, status board, and iteration report
6. run the task quality gate
7. if pass, set `DONE`
8. if fail, set `REVIEW` or `BLOCKED`, log the issue, and create `FX-*` if severity is high or critical
9. select the next task automatically

## Progress Locations

- `project_control/status_board.md`
- `project_control/backlog.yaml`
- `project_control/issue_register.yaml`
- `project_control/low_confidence_review_queue.yaml`
- `project_control/metrics.json`
- `reports/iteration_00_setup.md`
