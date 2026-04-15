# Project Control

This directory is the authoritative control plane for the autonomous publishing-remediation workflow.

## Read First

- `AGENTS.md`
- `.codex/config.toml`
- `project_control/backlog.yaml`
- `project_control/orchestration.md`
- `project_control/input_registry.yaml`

## Progress Monitoring

- `project_control/status_board.md`: current verdict, current task, next task, blocked tasks, open issues, and FX tasks
- `project_control/backlog.yaml`: normalized execution plan with owners, dependencies, statuses, quality gates, and done rules
- `project_control/input_registry.yaml`: detected repo-local inputs and current start mode
- `project_control/issue_register.yaml`: defects, blockers, regressions, and remediation links
- `project_control/low_confidence_review_queue.yaml`: uncertainty that must not be silently auto-closed
- `project_control/metrics.json`: operational counts plus BEFORE and AFTER quality metrics
- `project_control/repo_boundary.md`: hard boundary between `kindle master` and the separate `foreign frontend/runtime` frontend
- `reports/`: iteration-by-iteration execution log

## Rule

Do not execute conversion or remediation work outside the backlog-driven loop.
