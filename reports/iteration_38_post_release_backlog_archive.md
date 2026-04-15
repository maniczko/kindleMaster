# Iteration 38

## Summary

Switched the repository control plane from pre-release execution mode to post-release maintenance mode.

## What Changed

- copied the full historical task ledger into `project_control/backlog_archive.yaml`
- replaced `project_control/backlog.yaml` with a short active maintenance backlog
- updated orchestration rules so archived backlog history remains evidence, not an active queue
- updated `AGENTS.md` to explain READY maintenance behavior
- updated status board and metrics to point to the new active-vs-archive split

## Why

The repository is already `READY`, so the primary backlog should no longer read like an active crisis or recovery queue. The archive keeps the full evidence trail, while the active backlog stays readable for future maintenance work.

## Result

- historical execution evidence is preserved
- active backlog is readable again
- control plane remains explicit about current maintenance work and release status
