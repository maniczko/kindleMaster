## Kindle Master Repository Instructions

### Scope

This repository exists only for Kindle Master execution, recovery, testing, and release hardening.

Do not treat this repo as a mixed frontend/backend workspace.
Do not use external app runtimes as proof that Kindle Master works.

### Source Of Truth

- `AGENTS.md`
- `project_control/backlog.yaml`
- `project_control/orchestration.md`
- `project_control/status_board.md`
- `project_control/issue_register.yaml`
- `project_control/metrics.json`
- `project_control/publication_manifest.yaml`

### Key Rules

- no paraphrase
- no silent meaning change
- no evidence-free DONE
- no READY without passing release gates
- no unresolved foreign frontend or runtime coupling
- no destructive regeneration of final artifacts
